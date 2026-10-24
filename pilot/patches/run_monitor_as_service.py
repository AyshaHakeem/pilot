from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem

LEGACY_TIMERS = ("pilot-monitor.timer", "pilot-uptime.timer")
LEGACY_SERVICES = ("pilot-monitor.service", "pilot-uptime.service")


def run(benches_root: Path) -> None:
    """Replace the pilot-monitor and pilot-uptime timers with the long-running pilot-monitor
    service. The monitor is host-wide, so the first bench that has the old timer replaces it."""
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.systemd_user import user_timer_installed

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        if user_timer_installed("pilot-monitor.timer"):
            _replace_timers()
            print("Replaced the monitor and uptime timers with the pilot-monitor service.")
        mark_applied(bench_dir, PATCH_NAME)


def _replace_timers() -> None:
    import subprocess

    from pilot.core.server.monitoring_config import MonitorConfigurator
    from pilot.managers.systemd_user import systemctl, systemctl_env, user_unit_dir
    from pilot.utils import cli_root

    env = systemctl_env()
    subprocess.run(systemctl("disable", "--now", *LEGACY_TIMERS), capture_output=True, env=env, check=False)
    # A oneshot run in progress must finish before its unit becomes the long-running service.
    subprocess.run(systemctl("stop", *LEGACY_SERVICES), capture_output=True, env=env, check=False)

    unit_files = [
        cli_root() / "system" / "monitor" / "pilot-monitor.timer",
        cli_root() / "system" / "uptime" / "pilot-uptime.timer",
        cli_root() / "system" / "uptime" / "pilot-uptime.service",
        *(user_unit_dir() / name for name in (*LEGACY_TIMERS, *LEGACY_SERVICES)),
    ]
    for path in unit_files:
        path.unlink(missing_ok=True)

    MonitorConfigurator().install()
    # systemd keeps the removed uptime units in memory as failed until reset.
    subprocess.run(
        systemctl("reset-failed", *LEGACY_TIMERS, "pilot-uptime.service"),
        capture_output=True,
        env=env,
        check=False,
    )


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
