from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pilot.internal.tasks.worker as worker_module
from pilot.internal.tasks.worker_state import WorkerIntent, WorkerStore
from pilot.managers.task.control import TaskWorkerControl


def test_worker_control_start_request_persists_intent_and_wakes_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker_registry = Mock()
    monkeypatch.setattr(worker_module, "task_workers", worker_registry)
    WorkerStore(tmp_path).write_intent(WorkerIntent.STOPPED)

    TaskWorkerControl(tmp_path).request_start()

    assert WorkerStore(tmp_path).read_intent() == WorkerIntent.RUNNING
    worker_registry.wake.assert_called_once_with(tmp_path)


def test_worker_control_stop_request_persists_intent_and_wakes_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker_registry = Mock()
    monkeypatch.setattr(worker_module, "task_workers", worker_registry)

    TaskWorkerControl(tmp_path).request_stop()

    assert WorkerStore(tmp_path).read_intent() == WorkerIntent.STOPPED
    worker_registry.wake.assert_called_once_with(tmp_path)


def test_worker_control_starts_background_worker_and_signal_handlers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker_registry = Mock()
    monkeypatch.setattr(worker_module, "task_workers", worker_registry)

    TaskWorkerControl(tmp_path).start_background_worker()

    worker_registry.start.assert_called_once_with(tmp_path)
    worker_registry.install_signal_handlers.assert_called_once_with()


def test_worker_control_wake_uses_the_local_worker_when_there_is_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "task_workers", Mock(**{"wake.return_value": True}))

    with patch("pilot.managers.processes.local.ProcessManager.for_bench") as for_bench:
        TaskWorkerControl(tmp_path).wake()

    for_bench.assert_not_called()


def test_worker_control_wake_starts_the_admin_without_a_local_worker(tmp_path: Path, monkeypatch) -> None:
    """A CLI or task process has no worker. The Admin runs it, and it can be stopped on idle."""
    monkeypatch.setattr(worker_module, "task_workers", Mock(**{"wake.return_value": False}))

    with (
        patch("pilot.core.bench.Bench") as bench,
        patch("pilot.managers.processes.local.ProcessManager.for_bench") as for_bench,
    ):
        TaskWorkerControl(tmp_path).request_start()

    bench.assert_called_once_with(tmp_path)
    for_bench.assert_called_once_with(bench.return_value)
    for_bench.return_value.wake_admin.assert_called_once_with()


def test_the_worker_waits_for_the_database_before_its_first_task(tmp_path: Path, monkeypatch) -> None:
    """At boot the Admin can start before MariaDB, and a task run then would fail."""
    from pilot.core.bench.runtime import BenchRuntime
    from pilot.internal.tasks.worker import TaskWorker

    order: list[str] = []
    monkeypatch.setattr("pilot.core.bench.Bench", lambda root: root)
    monkeypatch.setattr(BenchRuntime, "wait_for_database", lambda self, timeout: order.append("database") or True)
    monkeypatch.setattr(TaskWorker, "_work", lambda self, pid: order.append("work"))

    worker = TaskWorker(tmp_path)
    worker.start()
    worker.join(5)

    assert order == ["database", "work"]
