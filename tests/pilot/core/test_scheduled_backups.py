from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import BackupConfig
from pilot.config.site_backup import write_retention
from pilot.core.bench.audit_log import AuditLog
from pilot.core.site import Site
from pilot.internal.tasks.store import TaskStore
from pilot.tasks.backup_site import BackupSiteTask
from tests.pilot.commands.test_commands import make_bench

_NOW = datetime(2026, 10, 18, 8, tzinfo=UTC)


def _site(tmp_path: Path) -> Site:
    bench = make_bench(tmp_path)
    bench.create_directories()
    bench.config.write(bench.path)
    site = bench.site("test.localhost")
    site.path.mkdir(parents=True)
    write_retention(site.path / "site_config.json", BackupConfig(calendar_rotation=True))
    return site


def _record(site: Site, when: datetime, **fields) -> None:
    AuditLog(site.bench).append(
        "backup",
        {
            "site": site.config.name, "event": "backup", "status": "success",
            "finished_at": when.isoformat(), "logged_at": when.isoformat(), **fields,
        },
    )


def test_first_poll_queues_a_logical_backup_with_files(tmp_path: Path) -> None:
    site = _site(tmp_path)

    assert site.backups.get_scheduled_backup(_NOW) == {"with_files": True}


def test_a_backup_in_the_last_23_hours_delays_the_next_one(tmp_path: Path) -> None:
    site = _site(tmp_path)
    _record(site, _NOW - timedelta(hours=1), with_files=True)

    assert site.backups.get_scheduled_backup(_NOW) is None


def test_a_daily_backup_stays_at_the_same_hour(tmp_path: Path) -> None:
    site = _site(tmp_path)
    _record(site, _NOW - timedelta(days=1) + timedelta(minutes=5), with_files=True)

    assert site.backups.get_scheduled_backup(_NOW) == {"with_files": True}
    assert site.backups.get_scheduled_backup(_NOW - timedelta(hours=1)) is None


def test_a_file_backup_earlier_today_skips_files(tmp_path: Path) -> None:
    site = _site(tmp_path)
    now = _NOW.replace(hour=23, minute=30)
    _record(site, now - timedelta(hours=23, minutes=1), with_files=True)

    assert site.backups.get_scheduled_backup(now) == {"with_files": False}


def test_the_next_day_includes_files_again(tmp_path: Path) -> None:
    site = _site(tmp_path)
    _record(site, _NOW - timedelta(hours=23), with_files=True)

    assert site.backups.get_scheduled_backup(_NOW) == {"with_files": True}


def test_failed_backups_are_retried_with_a_daily_limit(tmp_path: Path) -> None:
    site = _site(tmp_path)
    for attempt in range(5):
        _record(site, _NOW - timedelta(minutes=15 * attempt), status="failed")

    assert site.backups.get_scheduled_backup(_NOW) == {"with_files": True}
    _record(site, _NOW, status="failed")
    assert site.backups.get_scheduled_backup(_NOW) is None
    assert site.backups.get_scheduled_backup(_NOW + timedelta(days=1, minutes=1)) == {"with_files": True}


def test_sites_without_the_default_policy_are_not_scheduled(tmp_path: Path) -> None:
    site = _site(tmp_path)
    write_retention(site.path / "site_config.json", BackupConfig())

    assert site.backups.get_scheduled_backup(_NOW) is None


@pytest.mark.parametrize("manual_backup", [False, True])
def test_polls_do_not_overlap_a_queued_site_backup(tmp_path: Path, manual_backup: bool) -> None:
    site = _site(tmp_path)
    with patch("pilot.managers.task.control.TaskWorkerControl.wake"):
        if manual_backup:
            BackupSiteTask.queue(
                site.bench, site=site.config.name, with_files=True, resource_key=f"site:{site.config.name}"
            )
        site.bench.backups.queue_scheduled(_NOW)
        site.bench.backups.queue_scheduled(_NOW + timedelta(hours=1))

    task_dirs = list((site.bench.path / "tasks").glob("*/meta.json"))
    assert len(task_dirs) == 1
    metadata = TaskStore(site.bench.path).read_metadata(task_dirs[0].parent.name)
    assert metadata["command"] == "backup-site"
    assert metadata["resource_keys"] == [f"site:{site.config.name}"]


def test_failures_count_only_since_the_last_success(tmp_path: Path) -> None:
    site = _site(tmp_path)
    _record(site, _NOW - timedelta(hours=5), status="failed")
    _record(site, _NOW - timedelta(hours=4))
    for attempt in range(3):
        _record(site, _NOW - timedelta(hours=3 - attempt), status="failed")

    assert site.backups.get_failures_since_last_success(_NOW) == 3


@pytest.mark.parametrize(("earlier_failures", "notified"), [(4, False), (5, True), (6, False)])
def test_the_sixth_failure_since_the_last_success_notifies_once(
    tmp_path: Path, earlier_failures: int, notified: bool
) -> None:
    site = _site(tmp_path)
    now = datetime.now(UTC)
    for attempt in range(earlier_failures):
        _record(site, now - timedelta(hours=attempt + 1), status="failed")
    task = BackupSiteTask(bench=site.bench, bench_root=site.bench.path, site=site.config.name, automatic=True)

    with patch("pilot.core.site.backups.SiteBackups.take", side_effect=OSError("disk full")), pytest.raises(SystemExit):
        task.backup()

    events = [notification.event for notification in site.bench.notifications.list(50)]
    assert ("backups_failing" in events) is notified



def test_the_bench_poll_reads_history_once_and_queues_only_due_sites(tmp_path: Path) -> None:
    site = _site(tmp_path)
    backed_up = site.bench.site("backed-up.localhost")
    backed_up.path.mkdir(parents=True)
    write_retention(backed_up.path / "site_config.json", BackupConfig(calendar_rotation=True))
    unenrolled = site.bench.site("manual.localhost")
    unenrolled.path.mkdir(parents=True)
    _record(backed_up, _NOW - timedelta(hours=2), with_files=True)

    with (
        patch("pilot.managers.task.control.TaskWorkerControl.wake"),
        patch.object(AuditLog, "entries", wraps=AuditLog(site.bench).entries) as entries,
    ):
        site.bench.backups.queue_scheduled(_NOW)

    entries.assert_called_once()
    task_dirs = list((site.bench.path / "tasks").glob("*/meta.json"))
    queued = [TaskStore(site.bench.path).read_metadata(path.parent.name)["args"]["site"] for path in task_dirs]
    assert queued == [site.config.name]


def test_a_custom_schedule_is_not_queued_by_the_bench_job(tmp_path: Path) -> None:
    site = _site(tmp_path)
    with patch("pilot.managers.cron.CronManager.set_schedule"):
        site.backups.set_schedule("0 2 * * *", BackupConfig(scheme="gfs", keep_daily=3))

    assert site.backups.get_scheduled_backup(_NOW) is None

