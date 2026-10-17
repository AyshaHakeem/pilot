from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from pilot.managers.processes.definitions import ProcessDefinition
from pilot.managers.processes.supervisor import SupervisorRenderer
from pilot.managers.processes.systemd_render import SystemdRenderer


def _definition(argv: list[str] | None = None, **kwargs) -> ProcessDefinition:
    return ProcessDefinition(
        name="mail-stalwart", argv=argv or ["/bin/stalwart"], log_file=Path("/tmp/x.log"), **kwargs
    )


def test_systemd_renders_hooks_and_restart() -> None:
    unit = SystemdRenderer("bench").render(
        _definition(pre_run=["install.sh"], post_run=["/bin/rm", "-f", "s.sock"], restart_on_failure=False)
    )

    assert "ExecStartPre=/usr/bin/env install.sh\n" in unit
    assert "ExecStart=/bin/stalwart\n" in unit
    assert "ExecStopPost=/bin/rm -f s.sock\n" in unit
    assert "Restart=no\n" in unit


def test_systemd_escapes_specifiers_and_quotes() -> None:
    unit = SystemdRenderer("bench").render(
        _definition(argv=["/bin/stalwart", "--rate=100%", "$HOME"], env={"MESSAGE": 'say "50%"'})
    )

    assert "ExecStart=/bin/stalwart --rate=100%% '$$HOME'\n" in unit
    assert 'Environment="MESSAGE=say \\"50%%\\""\n' in unit


def test_supervisor_wraps_hooks_and_escapes_percent() -> None:
    program = SupervisorRenderer("bench", Path("/tmp")).render(
        _definition(
            argv=["/bin/stalwart", "--rate=100%"], pre_run=["/bin/install.sh"], env={"MESSAGE": '"50%"'}
        )
    )

    assert "command=sh -c '/bin/install.sh || exit; exec /bin/stalwart --rate=100%%'\n" in program
    assert 'environment=MESSAGE="\\"50%%\\""\n' in program
    assert "autorestart=unexpected\n" in program


def test_definition_without_hooks_runs_directly() -> None:
    assert _definition().argv_with_hooks == ["/bin/stalwart"]


def test_post_run_waits_for_a_terminated_process(tmp_path: Path) -> None:
    """Supervisor and the dev runner send TERM to the whole group; post_run must see the process gone."""
    marker = tmp_path / "running"
    process = [
        sys.executable,
        "-c",
        "import os, signal, sys, time\n"
        f"signal.signal(signal.SIGTERM, lambda *_: (time.sleep(0.5), os.remove({str(marker)!r}), sys.exit(0)))\n"
        f"open({str(marker)!r}, 'w').close()\n"
        "time.sleep(60)",
    ]
    check = f"test -e {marker} && echo running > {tmp_path}/result || echo stopped > {tmp_path}/result"
    definition = _definition(argv=process, post_run=["sh", "-c", check])

    wrapper = subprocess.Popen(definition.argv_with_hooks, start_new_session=True)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists()
    os.killpg(wrapper.pid, signal.SIGTERM)

    assert wrapper.wait(timeout=5) == 0
    assert (tmp_path / "result").read_text() == "stopped\n"


def test_wrapper_keeps_the_process_exit_code(tmp_path: Path) -> None:
    definition = _definition(argv=["sh", "-c", "exit 7"], post_run=["touch", str(tmp_path / "cleaned")])

    assert subprocess.run(definition.argv_with_hooks).returncode == 7
    assert (tmp_path / "cleaned").exists()


def test_post_run_runs_when_pre_run_fails(tmp_path: Path) -> None:
    definition = _definition(pre_run=["sh", "-c", "exit 3"], post_run=["touch", str(tmp_path / "cleaned")])

    assert subprocess.run(definition.argv_with_hooks).returncode == 3
    assert (tmp_path / "cleaned").exists()


def test_hooked_command_is_a_single_line() -> None:
    definition = _definition(pre_run=["install.sh"], post_run=["cleanup.sh"])

    assert "\n" not in definition.argv_with_hooks[2]
