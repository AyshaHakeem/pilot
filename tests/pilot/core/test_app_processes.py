from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config import AppConfig
from pilot.core.app import App
from pilot.core.app.processes import AppProcessError
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from tests.pilot.commands.test_commands import make_bench

_SECTION = "[tool.pilot.background_processes.stalwart]\n"


def _app(tmp_path: Path, pyproject: str) -> App:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "mail" / ".git").mkdir(parents=True)
    (bench.apps_path / "mail" / "pyproject.toml").write_text(pyproject)
    return App(AppConfig(name="mail", repo="https://github.com/frappe/mail", branch="main"), bench)


def test_reads_full_declaration(tmp_path: Path) -> None:
    app = _app(
        tmp_path,
        _SECTION + 'cmd = ["./stalwart", "--config", "config.toml"]\n'
        'pre_run = ["scripts/install.sh"]\n'
        'post_run = ["rm", "-f", "stalwart.sock"]\n'
        'working_dir = "/opt/stalwart"\n'
        "restart_on_failure = false\n"
        "stop_timeout = 30\n"
        'env = { STALWART_PATH = "/opt/stalwart" }\n',
    )

    (definition,) = app.processes.get_definitions()

    assert definition.name == "mail-stalwart"
    assert definition.argv == ["/opt/stalwart/stalwart", "--config", "config.toml"]
    assert definition.pre_run == ["/opt/stalwart/scripts/install.sh"]
    assert definition.post_run == ["rm", "-f", "stalwart.sock"]
    assert definition.working_dir == Path("/opt/stalwart")
    assert definition.restart_on_failure is False
    assert definition.stop_timeout == 30
    assert definition.env == {"STALWART_PATH": "/opt/stalwart"}
    assert definition.log_file == app.bench.logs_path / "mail-stalwart.log"
    assert definition.critical is False


def test_minimal_declaration_runs_in_the_app_directory(tmp_path: Path) -> None:
    app = _app(tmp_path, _SECTION + 'cmd = ["./stalwart"]\n')

    (definition,) = app.processes.get_definitions()

    assert definition.working_dir == app.path
    assert definition.argv == [str(app.path / "stalwart")]
    assert definition.restart_on_failure is True
    assert definition.pre_run == definition.post_run == []


@pytest.mark.parametrize("pyproject", ["", "[project]\nname = 'mail'\n"])
def test_app_without_declarations_has_no_processes(tmp_path: Path, pyproject: str) -> None:
    assert _app(tmp_path, pyproject).processes.get_definitions() == []


@pytest.mark.parametrize(
    "pyproject",
    [
        "[tool.pilot\n",
        "[tool.pilot]\nbackground_processes = 1\n",
        '[tool.pilot.background_processes."Bad Name"]\ncmd = ["/bin/true"]\n',
        _SECTION + "restart_on_failure = true\n",
        _SECTION + "cmd = []\n",
        _SECTION + 'cmd = "/bin/true"\n',
        _SECTION + 'cmd = ["/bin/true\\n[Service]"]\n',
        _SECTION + 'cmd = ["/bin/true"]\npre_run = "echo hi"\n',
        _SECTION + 'cmd = ["/bin/true"]\nenv = { "bad-key" = "v" }\n',
        _SECTION + 'cmd = ["/bin/true"]\nenv = { PORT = 8080 }\n',
        _SECTION + 'cmd = ["/bin/true"]\nrestart_on_failure = "yes"\n',
        _SECTION + 'cmd = ["/bin/true"]\nstop_timeout = true\n',
    ],
)
def test_malformed_declaration_is_rejected(tmp_path: Path, pyproject: str) -> None:
    with pytest.raises(AppProcessError, match="App 'mail'"):
        _app(tmp_path, pyproject).processes.get_definitions()


def test_bench_runs_declared_processes(tmp_path: Path) -> None:
    app = _app(tmp_path, _SECTION + 'cmd = ["/usr/bin/stalwart"]\n')
    builder = ProcessDefinitionBuilder(app.bench, Path("/usr/bin/python3"), watch_admin_js=False)

    names = [definition.name for definition in builder.prod_process_definitions()]

    assert "web" in names
    assert "mail-stalwart" in names
