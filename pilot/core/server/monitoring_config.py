from __future__ import annotations

import typing
from pathlib import Path

from pilot.config.monitor import (
    bench_log_path,
    db_log_path,
    monitor_log_dir,
    slow_query_log_path,
    system_log_path,
)
from pilot.exceptions import BenchError
from pilot.managers.platform import is_linux
from pilot.managers.systemd_user import SystemdUserMixin, install_user_service, user_service_installed
from pilot.utils import cli_root

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

MONITOR_DAEMON_TEMPLATE = """\
[Unit]
Description=bench monitor

[Service]
Type=simple
WorkingDirectory={cli_root}
Environment=PYTHONPATH={cli_root}
Environment=PYTHONUNBUFFERED=1
ExecStart={python} -m pilot.core.server.monitoring
Restart=always
RestartSec=5s
MemoryMax=128M
StandardOutput=append:{cli_root}/system/logs/monitor.log
StandardError=append:{cli_root}/system/logs/monitor.error.log

[Install]
WantedBy=default.target
"""


class MonitorConfigurator(SystemdUserMixin):
    """Installs the shared host-wide monitor service and configures per-bench
    log files. One long-running service runs host metrics and site uptime pings
    for every bench, so a tick does not pay for a new interpreter and its imports.
    install() is a no-op once it's already set up."""

    def __init__(self, bench: "Bench | None" = None):
        self.bench = bench
        self.unit_name = "pilot-monitor.service"
        self.monitor_dir = cli_root() / "system" / "monitor"

    def install(self) -> None:
        if user_service_installed(self.unit_name):
            return
        # systemd cannot open the unit's append: targets if this is missing.
        monitor_log_dir().mkdir(parents=True, exist_ok=True)
        install_user_service(
            unit_dir=self.monitor_dir, unit_name=self.unit_name, unit_text=self._render_unit()
        )

    @property
    def log_path(self) -> Path:
        return bench_log_path(self._require_bench().config.name)

    @property
    def system_log_path(self) -> Path:
        return system_log_path()

    @property
    def db_log_path(self) -> Path:
        return db_log_path()

    @property
    def slow_query_log_path(self) -> Path:
        return slow_query_log_path()

    def setup(self) -> None:
        if not is_linux():
            raise BenchError("Monitoring is only supported on linux based machines.")

        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _render_unit(self) -> str:
        from pilot.managers.environment import AdminEnvManager

        root = cli_root()
        return MONITOR_DAEMON_TEMPLATE.format(
            cli_root=root,
            python=AdminEnvManager(root).python,
        )

    def _require_bench(self) -> "Bench":
        assert self.bench is not None, "MonitorConfigurator needs a bench for this operation"
        return self.bench
