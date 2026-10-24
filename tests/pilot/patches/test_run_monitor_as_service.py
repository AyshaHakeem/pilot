from __future__ import annotations

from pathlib import Path

from pilot.core.server.monitoring_config import MonitorConfigurator
from pilot.patches.run_monitor_as_service import run


def _bench(root: Path, name: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(f'[bench]\nname = "{name}"\npython = "3.14"\n')


def test_timers_are_replaced_by_the_monitor_service_once(tmp_path: Path, monkeypatch) -> None:
    unit_dir = tmp_path / "user_units"
    unit_dir.mkdir()
    for name in (
        "pilot-monitor.timer",
        "pilot-monitor.service",
        "pilot-uptime.timer",
        "pilot-uptime.service",
    ):
        (unit_dir / name).write_text("")
    installs: list[str] = []
    monkeypatch.setattr("pilot.managers.systemd_user.user_unit_dir", lambda: unit_dir)
    monkeypatch.setattr("pilot.utils.cli_root", lambda: tmp_path)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: None)
    monkeypatch.setattr(MonitorConfigurator, "install", lambda self: installs.append("monitor"))
    root = tmp_path / "benches"
    _bench(root, "one")
    _bench(root, "two")

    run(root)
    run(root)

    assert installs == ["monitor"]
    assert list(unit_dir.iterdir()) == []
