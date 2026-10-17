from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from pilot.managers.cron import CronManager
from pilot.managers.environment import AdminEnvManager
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.processes.base import (
    ManagedProcessManager,
    ServiceRenderer,
    UnitGroup,
    override,
)
from pilot.managers.processes.definitions import ProcessState
from pilot.managers.processes.local import ProcessDefinition
from pilot.utils import cli_root, run_command

_BOOT_JOB = "supervisord-boot"
# `group:program   RUNNING   pid 123, uptime 0:01:02`
_STATUS_LINE = re.compile(r"^(?P<program>\S+)\s+(?P<state>\S+)(?:\s+pid (?P<pid>\d+),)?")


def is_redis(pd: ProcessDefinition) -> bool:
    return pd.app is None and pd.name.startswith("redis")


class SupervisorRenderer(ServiceRenderer):
    """Builds the bench's supervisord.conf and per-program blocks."""

    def __init__(self, bench_name: str, log_dir) -> None:
        super().__init__(bench_name)
        self.log_dir = log_dir

    @override
    def render(self, pd: ProcessDefinition) -> str:
        directory = f"directory={pd.working_dir}\n" if pd.working_dir else ""
        pairs = []
        for key, value in pd.env.items():
            value = value.replace("%", "%%").replace('"', '\\"')
            pairs.append(f'{key}="{value}"')
        env = f"environment={','.join(pairs)}\n" if pairs else ""
        stop = f"stopwaitsecs={pd.stop_timeout}\n" if pd.stop_timeout is not None else ""
        return (
            f"[program:{self.get_program_name(pd.name)}]\n"
            # supervisord expands %(name)s in every value, quoted or not.
            f"command={shlex.join(pd.argv_with_hooks).replace('%', '%%')}\n"
            f"{env}{directory}"
            f"autostart=true\n"
            # unexpected: restart on a non-zero exit only, like systemd Restart=on-failure.
            f"autorestart={'unexpected' if pd.restart_on_failure else 'false'}\n"
            f"startretries=3\n"
            f"stdout_logfile={self.log_dir}/{pd.name}.log\n"
            f"stderr_logfile={self.log_dir}/{pd.name}.error.log\n"
            f"stopasgroup=true\n"
            f"killasgroup=true\n"
            f"{stop}"
        )

    def render_supervisord_conf(self, defs: list[ProcessDefinition], sock, pid) -> str:
        # A group stops all its programs at once, and `update` restarts every program of a group
        # whose list changed. So redis and app programs get their own groups.
        redis = [pd for pd in defs if is_redis(pd)]
        admin = [pd for pd in defs if pd.name == "admin"]
        apps = [pd for pd in defs if pd.app is not None]
        workload = [pd for pd in defs if pd not in (*redis, *admin, *apps)]
        groups = (
            f"[group:{self.bench_name}]\nprograms={','.join(self.get_program_name(pd.name) for pd in workload)}\n\n"
            # supervisord starts and shuts down whole groups by priority; lower starts first, stops last.
            + self._group(f"{self.bench_name}-redis", redis, "priority=100\n")
            + self._group(f"{self.bench_name}-admin", admin)
            + self._group(f"{self.bench_name}-apps", apps)
        )
        programs = "\n".join(self.render(pd) for pd in defs)
        include_glob = Path(sock).parent / "supervisor.d" / "*.conf"
        return (
            f"[unix_http_server]\n"
            f"file={sock}\n"
            f"chmod=0700\n\n"
            f"[supervisord]\n"
            f"logfile={self.log_dir}/supervisord.log\n"
            f"logfile_maxbytes=50MB\n"
            f"logfile_backups=10\n"
            f"loglevel=info\n"
            f"pidfile={pid}\n"
            f"nodaemon=false\n\n"
            f"[rpcinterface:supervisor]\n"
            f"supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface\n\n"
            f"[supervisorctl]\n"
            f"serverurl=unix://{sock}\n\n"
            f"[include]\n"
            f"files={include_glob}\n\n"
            f"{groups}"
            f"{programs}"
        )

    def get_program_name(self, name: str) -> str:
        return f"{self.bench_name}-{name.replace('_', '-')}"

    def _group(self, name: str, items: list[ProcessDefinition], extra: str = "") -> str:
        if not items:
            return ""
        return f"[group:{name}]\nprograms={','.join(self.get_program_name(pd.name) for pd in items)}\n{extra}\n"


class SupervisorProcessManager(ManagedProcessManager):
    """Manages bench processes via a bench-owned supervisord instance (no sudo required)."""

    @property
    def supervisor_dir(self) -> Path:
        return self.bench.config_path / "services"

    @property
    def supervisor_conf_path(self) -> Path:
        return self.supervisor_dir / "supervisord.conf"

    @property
    def supervisor_include_dir(self) -> Path:
        """Stable extension directory preserved across Pilot regeneration."""
        return self.supervisor_dir / "supervisor.d"

    @property
    def supervisor_sock(self) -> Path:
        return self.supervisor_dir / "supervisord.sock"

    @property
    def supervisor_pid(self) -> Path:
        return self.supervisor_dir / "supervisord.pid"

    @property
    def workload_group(self) -> str:
        return self.bench.config.name

    @property
    def admin_group(self) -> str:
        return f"{self.bench.config.name}-admin"

    @property
    def apps_group(self) -> str:
        return f"{self.bench.config.name}-apps"

    @property
    def redis_group(self) -> str:
        return f"{self.bench.config.name}-redis"

    @override
    def write_config(self) -> None:
        AdminEnvManager(cli_root()).ensure()
        self._ensure_redis_config()
        self._ensure_gunicorn_config()
        GunicornManager(self.bench).generate_admin_config()
        self.supervisor_dir.mkdir(parents=True, exist_ok=True)
        self.supervisor_include_dir.mkdir(parents=True, exist_ok=True)
        renderer = SupervisorRenderer(self.bench.config.name, self.bench.logs_path)
        self.supervisor_conf_path.write_text(
            renderer.render_supervisord_conf(self._prod_process_definitions(), self.supervisor_sock, self.supervisor_pid)
        )

    @override
    def install_config(self) -> None:
        self.supervisor_dir.mkdir(parents=True, exist_ok=True)
        # The bench owns its supervisord, so nothing else starts it after a reboot.
        CronManager(self.bench.path).set_schedule(_BOOT_JOB, "@reboot", self._boot_command())

    def _boot_command(self) -> str:
        supervisord = shutil.which("supervisord") or "supervisord"
        log_file = self.bench.logs_path / "supervisord-boot.log"
        return f"{shlex.quote(supervisord)} -c {shlex.quote(str(self.supervisor_conf_path))} >> {shlex.quote(str(log_file))} 2>&1"

    @override
    def reload_manager_config(self) -> None:
        if self.is_alive():
            run_command([*self._supervisorctl(), "reread"])
            run_command([*self._supervisorctl(), "update"])

    @override
    def ensure_ready(self) -> None:
        if self.is_alive():
            run_command([*self._supervisorctl(), "reread"])
            run_command([*self._supervisorctl(), "update"])
        else:
            run_command(["supervisord", "-c", str(self.supervisor_conf_path)])

    @override
    def apply_unit_action(self, action: str, role: UnitGroup) -> None:
        # stop/admin-restart are no-ops when the daemon is down to avoid supervisorctl errors.
        if (action == "stop" or (action == "restart" and role is UnitGroup.ADMIN)) and not self.is_alive():
            return
        # supervisorctl handles one group at a time, in order: redis stops last and starts first.
        targets = self.get_targets(role)
        if action in ("stop", "restart"):
            run_command([*self._supervisorctl(), "stop", *targets])
        if action in ("start", "restart"):
            run_command([*self._supervisorctl(), "start", *reversed(targets)])

    @override
    def are_units_running(self, role: UnitGroup) -> bool:
        if not self.is_configured() or not self.is_alive():
            return False
        group = self.admin_group if role is UnitGroup.ADMIN else self.workload_group
        result = subprocess.run(
            [*self._supervisorctl(), "status", f"{group}:*"],
            capture_output=True,
            text=True,
        )
        return "RUNNING" in result.stdout

    @override
    def get_process_states(self, definitions: list[ProcessDefinition]) -> dict[str, ProcessState]:
        if not self.is_alive():
            return {}
        programs = {}
        for definition in definitions:
            group, _, program = self._program(definition).partition(":")
            # supervisorctl prints a program alone when its group has the same name, as admin does.
            programs[program if group == program else f"{group}:{program}"] = definition.name
        result = subprocess.run([*self._supervisorctl(), "status", *programs], capture_output=True, text=True)
        states = {}
        for line in result.stdout.splitlines():
            match = _STATUS_LINE.match(line)
            if match and match["program"] in programs:
                pid = int(match["pid"]) if match["pid"] else None
                states[programs[match["program"]]] = ProcessState(match["state"], pid)
        return states

    @override
    def restart_processes(self, definitions: list[ProcessDefinition]) -> None:
        run_command([*self._supervisorctl(), "restart", *(self._program(definition) for definition in definitions)])

    def _program(self, definition: ProcessDefinition) -> str:
        if definition.name == "admin":
            group = self.admin_group
        elif definition.app:
            group = self.apps_group
        else:
            group = self.redis_group if is_redis(definition) else self.workload_group
        program = SupervisorRenderer(self.bench.config.name, self.bench.logs_path).get_program_name(definition.name)
        return f"{group}:{program}"

    def is_configured(self) -> bool:
        return self.supervisor_conf_path.exists()

    def is_alive(self) -> bool:
        if not self.supervisor_pid.exists():
            return False
        try:
            pid = int(self.supervisor_pid.read_text().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, OSError):
            return False

    def shutdown(self) -> None:
        """Tear down everything, including the admin group and the daemon."""
        CronManager(self.bench.path).remove_schedule(_BOOT_JOB)
        if self.is_alive():
            run_command([*self._supervisorctl(), "shutdown"])

    def _supervisorctl(self) -> list[str]:
        return ["supervisorctl", "-c", str(self.supervisor_conf_path)]

    def get_targets(self, role: UnitGroup) -> list[str]:
        if role is UnitGroup.ADMIN:
            return [f"{self.admin_group}:*"]
        if role is UnitGroup.WEB:
            return [f"{self.bench.config.name}:{self.bench.config.name}-web"]
        targets = [f"{self.workload_group}:*"]
        # supervisorctl rejects a group it does not have, and the apps group exists only while one runs.
        if f"[group:{self.apps_group}]" in self.supervisor_conf_path.read_text():
            targets.append(f"{self.apps_group}:*")
        return [*targets, f"{self.redis_group}:*"]
