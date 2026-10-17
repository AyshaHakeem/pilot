from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.managers.processes.definitions import ProcessState
from pilot.managers.processes.local import ProcessManager

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pilot.core.app import App
    from pilot.core.bench import Bench
    from pilot.managers.processes.definitions import ProcessDefinition

BENCH = "bench"
STOPPED = ProcessState("stopped")


@dataclass(frozen=True)
class ProcessRow:
    name: str
    app: str  # BENCH for the bench's own processes
    state: ProcessState
    command: str


class BenchProcesses:
    """The bench's processes, and the app processes that follow the apps its sites use."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def active_apps(self) -> set[str]:
        """Module names of the apps at least one site uses."""
        return {app for site in self.bench.sites() for app in site.active_apps()}

    def get_declared(self, app_name: str | None = None) -> dict["App", list["ProcessDefinition"]]:
        """Each app's declared processes, whether or not a site uses the app."""
        apps = [self.bench.app(app_name)] if app_name else self.bench.apps()
        declared = {app: app.processes.get_definitions() for app in apps}
        return {app: definitions for app, definitions in declared.items() if definitions}

    def get_app_definitions(self) -> list["ProcessDefinition"]:
        """The app processes that run: declared by an app a site uses, and not disabled."""
        declared = self.get_declared()
        if not declared:
            return []

        active = self.active_apps
        disabled = set(self.bench.config.processes.disabled)
        return [
            definition
            for app, definitions in declared.items()
            if app.module_name in active
            for definition in definitions
            if definition.name not in disabled
        ]

    def has_declarations(self, app_names: "Iterable[str]") -> bool:
        """Whether any of these apps, by app or module name, declares a process. Parses only those apps."""
        names = set(app_names)
        apps = [app for app in self.bench.apps() if app.config.name in names or app.module_name in names]
        return any(app.processes.get_definitions() for app in apps)

    def apply(self) -> None:
        """Write, link and start the production process set. Running processes keep running;
        a new app process starts, and a removed one stops and is unlinked."""
        from pilot.managers.processes.base import UnitGroup

        manager = ProcessManager.for_bench(self.bench)
        if not self.bench.config.production.enabled or not manager.is_configured():
            return

        manager.write_config()
        manager.install_config()
        manager.reload_manager_config()
        if manager.is_running():
            manager.apply_unit_action("start", UnitGroup.WORKLOAD)

    def restart(self, names: list[str]) -> list[str]:
        """Restart the named processes. An app name stands for every process of that app that runs."""
        manager = ProcessManager.for_bench(self.bench)
        app_of = self._declared_apps()
        running = {
            definition.name: app_of.get(definition.name, BENCH) for definition in manager.process_definitions
        }
        process_names = self._resolve(names, running)
        manager.restart_processes(process_names)
        return process_names

    def disable(self, names: list[str]) -> list[str]:
        """Stop app processes and keep them off across restarts, updates and installs."""
        process_names = self._resolve(names, self._declared_apps())
        config = self.bench.config.processes
        config.disabled = sorted(set(config.disabled) | set(process_names))
        self.bench.config.write(self.bench.path)
        self.apply()
        return process_names

    def enable(self, names: list[str]) -> list[str]:
        """Undo disable. The process starts again if a site uses its app."""
        process_names = self._resolve(names, self._declared_apps())
        config = self.bench.config.processes
        config.disabled = [name for name in config.disabled if name not in process_names]
        self.bench.config.write(self.bench.path)
        self.apply()
        return process_names

    def get_rows(self, app_name: str | None = None) -> list[ProcessRow]:
        """Each process with its state. A declared process that does not run says why."""
        manager = ProcessManager.for_bench(self.bench)
        declared = self.get_declared(app_name)
        app_of = {
            definition.name: app.config.name
            for app, definitions in declared.items()
            for definition in definitions
        }
        runs = {definition.name for definition in self.get_app_definitions()} if declared else set()

        bench_definitions = (
            [] if app_name else [d for d in manager.process_definitions if d.name not in app_of]
        )
        app_definitions = [definition for definitions in declared.values() for definition in definitions]
        names = [d.name for d in bench_definitions] + [d.name for d in app_definitions if d.name in runs]
        states = manager.get_process_states(names)

        rows = [
            self._row(definition, BENCH, states.get(definition.name, STOPPED))
            for definition in bench_definitions
        ]
        for definition in app_definitions:
            state = (
                states.get(definition.name, STOPPED)
                if definition.name in runs
                else self._idle_state(definition.name)
            )
            rows.append(self._row(definition, app_of[definition.name], state))
        return rows

    def _idle_state(self, name: str) -> ProcessState:
        """Why a declared process does not run."""
        if name in self.bench.config.processes.disabled:
            return ProcessState("disabled")
        return ProcessState("no site uses the app")

    @staticmethod
    def _row(definition: "ProcessDefinition", app: str, state: ProcessState) -> ProcessRow:
        return ProcessRow(definition.name, app, state, shlex.join(definition.argv))

    def _declared_apps(self) -> dict[str, str]:
        """Each declared app process name, mapped to its app."""
        return {
            definition.name: app.config.name
            for app, definitions in self.get_declared().items()
            for definition in definitions
        }

    @staticmethod
    def _resolve(names: list[str], processes: dict[str, str]) -> list[str]:
        """Process names from `names`, where an app name stands for each of its processes."""
        resolved: list[str] = []
        for name in names:
            matches = [process for process, app in processes.items() if name in (process, app)]
            if not matches:
                known = ", ".join(sorted(processes)) or "none"
                raise BenchError(f"No process or app named '{name}'. Processes: {known}.")
            resolved += [match for match in matches if match not in resolved]
        return resolved
