from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config import BenchConfig
from pilot.core.bench.processes import BenchProcesses
from pilot.exceptions import BenchError
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from tests.pilot.commands.test_commands import make_bench

_MAIL = '[tool.pilot.processes.stalwart]\ncommand = ["/usr/bin/stalwart"]\n[tool.pilot.processes.relay]\ncommand = ["relay"]\n'


@pytest.fixture
def bench(tmp_path: Path, monkeypatch):
    bench = make_bench(tmp_path)
    bench.create_directories()
    bench.config.write(bench.path)
    for name, pyproject in {"mail": _MAIL, "crm": "[project]\nname = 'crm'\n"}.items():
        (bench.apps_path / name / ".git").mkdir(parents=True)
        (bench.apps_path / name / "pyproject.toml").write_text(pyproject)
    monkeypatch.setattr(BenchProcesses, "active_apps", property(lambda self: {"mail"}))
    return bench


def _running(bench) -> list[str]:
    builder = ProcessDefinitionBuilder(bench, Path("/usr/bin/python3"), watch_admin_js=False)
    return [definition.name for definition in builder.prod_process_definitions()]


def test_runs_the_processes_of_apps_a_site_uses(bench, monkeypatch) -> None:
    assert {"web", "mail-stalwart", "mail-relay"} <= set(_running(bench))

    monkeypatch.setattr(BenchProcesses, "active_apps", property(lambda self: set()))
    assert not any(name.startswith("mail-") for name in _running(bench))


def test_disable_and_enable_are_saved_and_take_an_app_name(bench) -> None:
    assert bench.processes.disable(["mail-relay"]) == ["mail-relay"]
    assert "mail-relay" not in _running(bench)
    assert BenchConfig.read(bench.path).processes.disabled == ["mail-relay"]

    assert bench.processes.enable(["mail"]) == ["mail-stalwart", "mail-relay"]
    assert bench.config.processes.disabled == []


def test_unknown_process_is_rejected(bench) -> None:
    with pytest.raises(BenchError, match="No process or app named 'crm'"):
        bench.processes.disable(["crm"])


def test_rows_say_why_a_declared_process_does_not_run(bench, monkeypatch) -> None:
    bench.processes.disable(["mail-relay"])
    monkeypatch.setattr(BenchProcesses, "active_apps", property(lambda self: set()))

    rows = {row.name: row for row in bench.processes.get_rows("mail")}

    assert rows["mail-relay"].state.state == "disabled"
    assert rows["mail-stalwart"].state.state == "no site uses the app"
    assert rows["mail-stalwart"].app == "mail"


def test_a_broken_app_does_not_block_checking_another(bench) -> None:
    (bench.apps_path / "crm" / "pyproject.toml").write_text("[tool.pilot\n")

    assert bench.processes.has_declarations(["mail"])
