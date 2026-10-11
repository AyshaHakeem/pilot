from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pilot.exceptions import BenchError
from pilot.utils import get_node_tool_bin, install_node_tool, run_command


@dataclass(frozen=True)
class NodePackageManager:
    """A JS package manager, known by the lockfile it writes."""

    name: str
    lockfile: str
    install_marker: str
    """A file the manager rewrites in node_modules on every install."""
    install_args: tuple[str, ...]
    frozen_install_args: tuple[str, ...]

    @property
    def bin(self) -> str:
        try:
            return get_node_tool_bin(self.name)
        except BenchError:
            install_node_tool(self.name)
            return get_node_tool_bin(self.name)


YARN = NodePackageManager(
    name="yarn",
    lockfile="yarn.lock",
    install_marker=".yarn-integrity",
    install_args=("install",),
    frozen_install_args=("install", "--frozen-lockfile"),
)
PNPM = NodePackageManager(
    name="pnpm",
    lockfile="pnpm-lock.yaml",
    install_marker=".modules.yaml",
    install_args=("install",),
    # Without a TTY, pnpm stops instead of asking to replace a node_modules another manager wrote.
    frozen_install_args=("install", "--frozen-lockfile", "--config.confirmModulesPurge=false"),
)
PACKAGE_MANAGERS = (YARN, PNPM)


class NodePackages:
    """JS dependencies of a package directory, installed by the manager its lockfile names.

    A workspace member without its own lockfile installs from the workspace root at `app_path`.
    A directory without any lockfile installs with yarn, as Frappe does.
    """

    def __init__(self, path: Path, app_path: Path | None = None) -> None:
        self.path = path
        self.app_path = app_path or path

    @property
    def install_path(self) -> Path:
        if self.lockfiles_in(self.path) or self.path == self.app_path:
            return self.path
        if self.is_workspace_root(self.app_path) and self.lockfiles_in(self.app_path):
            return self.app_path
        return self.path

    @property
    def lockfile(self) -> Path | None:
        lockfiles = self.lockfiles_in(self.install_path)
        if len(lockfiles) > 1:
            names = " and ".join(lockfile.name for lockfile in lockfiles)
            raise BenchError(
                f"{self.install_path} has both {names}. Keep the lockfile of one package manager."
            )
        return lockfiles[0] if lockfiles else None

    @property
    def package_manager(self) -> NodePackageManager:
        lockfile = self.lockfile
        if lockfile is None:
            return YARN
        return next(manager for manager in PACKAGE_MANAGERS if manager.lockfile == lockfile.name)

    @property
    def is_installed(self) -> bool:
        """Whether node_modules was installed after the lockfile last changed."""
        marker = self.install_path / "node_modules" / self.package_manager.install_marker
        lockfile = self.lockfile
        if not marker.exists():
            return False
        return lockfile is None or lockfile.stat().st_mtime <= marker.stat().st_mtime

    def install(self, frozen: bool = True) -> None:
        manager = self.package_manager
        if self.lockfile is None:
            print(f"  {self.install_path} has no lockfile; {manager.name} installs unpinned versions.")
            sys.stdout.flush()
        args = manager.frozen_install_args if frozen else manager.install_args
        run_command([manager.bin, *args], cwd=self.install_path, stream_output=True)

    def ensure_installed(self) -> None:
        if not self.is_installed:
            print(f"  Installing JS dependencies in {self.install_path}...")
            sys.stdout.flush()
            self.install()

    def get_script_command(self, script: str) -> list[str]:
        """The command that runs a package.json script in `path`."""
        return [self.package_manager.bin, "run", script]

    @property
    def has_build_script(self) -> bool:
        package_json = self.path / "package.json"
        if not package_json.exists():
            return False
        return "build" in json.loads(package_json.read_text()).get("scripts", {})

    @staticmethod
    def lockfiles_in(path: Path) -> list[Path]:
        return [path / manager.lockfile for manager in PACKAGE_MANAGERS if (path / manager.lockfile).exists()]

    @staticmethod
    def is_workspace_root(path: Path) -> bool:
        if (path / "pnpm-workspace.yaml").exists():
            return True
        package_json = path / "package.json"
        return package_json.exists() and "workspaces" in json.loads(package_json.read_text())
