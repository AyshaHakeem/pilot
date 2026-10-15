from __future__ import annotations

from pathlib import Path

from pilot.config import (
    AppConfig,
    BenchConfig,
    LiteModeConfig,
    MariaDBConfig,
    RedisConfig,
    SiteConfig,
    WorkerConfig,
)
from pilot.core.bench import Bench
from pilot.managers.nginx import NginxConfigRenderer
from pilot.managers.processes.definitions import ProcessDefinitionBuilder
from pilot.managers.processes.systemd_render import SystemdRenderer


def make_bench(tmp_path: Path, **config) -> Bench:
    bench = Bench(
        BenchConfig(
            name="test-bench",
            python_version="3.14",
            apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
            mariadb=MariaDBConfig(root_password="root"),
            redis=RedisConfig(cache_port=13000, queue_port=11000),
            workers=WorkerConfig(),
            lite_mode=LiteModeConfig(enabled=False),
            **config,
        ),
        tmp_path,
    )
    bench.config.production.process_manager = "systemd"
    return bench


def socketio(bench: Bench, dev: bool = False):
    return ProcessDefinitionBuilder(bench, Path("/env/bin/python"), False).socketio_definition(dev=dev)


def test_production_realtime_listens_on_a_unix_socket(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    socket = bench.config_path / "socketio.sock"

    definition = socketio(bench)
    unit = SystemdRenderer("test-bench").render(definition)

    assert bench.realtime_socket == socket
    assert definition.env == {"FRAPPE_SOCKETIO_UDS": str(socket)}
    assert f"ExecStartPre=/bin/rm -f {socket}\n" in unit
    assert unit.index("ExecStartPre=") < unit.index("ExecStart=")


def test_development_realtime_keeps_its_port(tmp_path: Path) -> None:
    definition = socketio(make_bench(tmp_path), dev=True)

    assert definition.env == {}
    assert "ExecStartPre" not in SystemdRenderer("test-bench").render(definition)


def test_the_python_backend_keeps_its_port(tmp_path: Path) -> None:
    bench = make_bench(tmp_path, socketio_backend="python")

    assert bench.realtime_socket is None
    assert "FRAPPE_SOCKETIO_UDS" not in socketio(bench).env


def test_supervisor_keeps_the_realtime_port(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.production.process_manager = "supervisor"

    assert bench.realtime_socket is None


def test_nginx_proxies_realtime_to_the_socket(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    renderer = NginxConfigRenderer(bench)
    renderer._proxy_servers_cache = []
    site = SiteConfig(name="site1.example.com", apps=["frappe"])

    config = renderer.generate_bench_config([(site, [])], admin_ssl=False)

    assert f"proxy_pass         http://unix:{bench.config_path / 'socketio.sock'}:;" in config
    assert "127.0.0.1:9000" not in config


def test_a_long_socket_path_keeps_the_port(tmp_path: Path) -> None:
    bench = make_bench(tmp_path / ("é" * 48))

    assert bench.realtime_socket is None
