"""Tests for NodePackages: which manager installs an app's JS dependencies, and where."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.app.node_packages import NodePackages
from pilot.exceptions import BenchError, CommandError


@pytest.fixture
def run_command() -> Iterator[MagicMock]:
    with (
        patch("pilot.core.app.node_packages.get_node_tool_bin", side_effect=lambda name: name),
        patch("pilot.core.app.node_packages.run_command") as run_command,
    ):
        yield run_command


def write_package(path: Path, **package: object) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_text(json.dumps(package))
    return path


def test_yarn_lock_installs_frozen_with_yarn(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "yarn.lock").write_text("")

    NodePackages(app).install()

    run_command.assert_called_once_with(["yarn", "install", "--frozen-lockfile"], cwd=app, stream_output=True)


def test_pnpm_lock_installs_frozen_with_pnpm(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "pnpm-lock.yaml").write_text("")

    NodePackages(app).install()

    run_command.assert_called_once_with(
        ["pnpm", "install", "--frozen-lockfile", "--config.confirmModulesPurge=false"],
        cwd=app,
        stream_output=True,
    )


def test_workspace_member_installs_from_the_workspace_root(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "pnpm-workspace.yaml").write_text("packages:\n  - frontend\n")
    (app / "pnpm-lock.yaml").write_text("")
    frontend = write_package(app / "frontend", scripts={"build": "vite build"})

    packages = NodePackages(frontend, app)
    packages.install()

    assert run_command.call_args.args[0][0] == "pnpm"
    assert run_command.call_args.kwargs["cwd"] == app
    assert packages.get_script_command("build") == ["pnpm", "run", "build"]


def test_member_of_a_plain_app_installs_in_its_own_directory(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "pnpm-lock.yaml").write_text("")
    frontend = write_package(app / "frontend")
    (frontend / "yarn.lock").write_text("")

    NodePackages(frontend, app).install()

    run_command.assert_called_once_with(
        ["yarn", "install", "--frozen-lockfile"], cwd=frontend, stream_output=True
    )


def test_two_lockfiles_fail_before_installing(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "yarn.lock").write_text("")
    (app / "pnpm-lock.yaml").write_text("")

    with pytest.raises(BenchError, match=r"yarn\.lock and pnpm-lock\.yaml"):
        NodePackages(app).install()
    run_command.assert_not_called()


def test_failed_frozen_install_is_not_retried(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "yarn.lock").write_text("")
    run_command.side_effect = CommandError("lockfile needs update", returncode=1)

    with pytest.raises(CommandError):
        NodePackages(app).ensure_installed()
    run_command.assert_called_once()


@pytest.mark.parametrize(
    ("lockfile", "marker"), [("yarn.lock", ".yarn-integrity"), ("pnpm-lock.yaml", ".modules.yaml")]
)
def test_install_runs_again_only_after_the_lockfile_changes(
    tmp_path: Path, run_command: MagicMock, lockfile: str, marker: str
) -> None:
    app = write_package(tmp_path / "app")
    (app / lockfile).write_text("")
    (app / "node_modules").mkdir()
    (app / "node_modules" / marker).write_text("")
    os.utime(app / lockfile, (1, 1))
    os.utime(app / "node_modules" / marker, (2, 2))

    NodePackages(app).ensure_installed()
    run_command.assert_not_called()

    os.utime(app / lockfile, (3, 3))
    NodePackages(app).ensure_installed()
    run_command.assert_called_once()


def test_missing_pnpm_is_installed_first(tmp_path: Path) -> None:
    app = write_package(tmp_path / "app")
    (app / "pnpm-lock.yaml").write_text("")
    installed: list[str] = []

    def find(name: str) -> str:
        if name not in installed:
            raise BenchError(f"{name} not found")
        return f"/home/user/.local/bin/{name}"

    with (
        patch("pilot.core.app.node_packages.get_node_tool_bin", side_effect=find),
        patch("pilot.core.app.node_packages.install_node_tool", side_effect=installed.append),
        patch("pilot.core.app.node_packages.run_command") as run_command,
    ):
        NodePackages(app).install()

    assert installed == ["pnpm"]
    assert run_command.call_args.args[0][0] == "/home/user/.local/bin/pnpm"
