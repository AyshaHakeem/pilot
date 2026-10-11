from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
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
    # Without a TTY, pnpm stops instead of asking to replace a node_modules another manager wrote.
    install_args=("install", "--config.confirmModulesPurge=false"),
    frozen_install_args=("install", "--frozen-lockfile", "--config.confirmModulesPurge=false"),
)
PACKAGE_MANAGERS = (YARN, PNPM)


class NodePackages:
    """JS dependencies of a package directory, installed by the manager its lockfile names.

    A member of the workspace at `app_path` without its own lockfile installs from that root.
    A directory without any lockfile installs with yarn, as Frappe does.
    """

    def __init__(self, path: Path, app_path: Path | None = None) -> None:
        self.path = path
        self.app_path = app_path or path

    @property
    def install_path(self) -> Path:
        if self.lockfiles_in(self.path) or self.path == self.app_path:
            return self.path
        if self.is_workspace_member:
            return self.app_path
        return self.path

    @property
    def is_workspace_member(self) -> bool:
        """Whether a workspace pattern at `app_path` matches `path`, and no `!` pattern excludes it."""
        member = self.path.relative_to(self.app_path).as_posix()
        patterns = [pattern.strip().removeprefix("./").rstrip("/") for pattern in self.workspace_patterns]
        included = any(fnmatch(member, pattern) for pattern in patterns if not pattern.startswith("!"))
        return included and not any(
            fnmatch(member, pattern[1:]) for pattern in patterns if pattern.startswith("!")
        )

    @property
    def workspace_patterns(self) -> list[str]:
        """Member globs from `package.json` workspaces and `pnpm-workspace.yaml` packages."""
        patterns: list[str] = []
        package_json = self.app_path / "package.json"
        if package_json.exists():
            workspaces = json.loads(package_json.read_text()).get("workspaces", [])
            patterns += workspaces.get("packages", []) if isinstance(workspaces, dict) else workspaces
        pnpm_workspace = self.app_path / "pnpm-workspace.yaml"
        if pnpm_workspace.exists():
            patterns += self.read_pnpm_workspace_packages(pnpm_workspace.read_text())
        return patterns

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
    def read_pnpm_workspace_packages(text: str) -> list[str]:
        """The block list under the top-level `packages:` key. Pilot has no YAML parser."""
        packages: list[str] = []
        in_packages = False
        for line in text.splitlines():
            content = line.split("#", 1)[0].rstrip()
            item = content.strip()
            if content and not content[0].isspace() and not item.startswith("-"):
                in_packages = item == "packages:"
            elif in_packages and item.startswith("- "):
                packages.append(item[2:].strip().strip("'\""))
        return packages
