from __future__ import annotations

import json
from pathlib import Path

from pilot.patches.check_realtime_sessions_locally import run


def test_points_realtime_at_local_web_once(tmp_path: Path) -> None:
    bench_dir = tmp_path / "main"
    (bench_dir / "sites").mkdir(parents=True)
    (bench_dir / "bench.toml").write_text('[bench]\nname = "main"\npython = "3.14"\n')
    config_path = bench_dir / "sites" / "common_site_config.json"
    config_path.write_text('{"webserver_port": 8000}')

    run(tmp_path)
    assert json.loads(config_path.read_text()) == {"webserver_port": 8000, "webserver_host": "127.0.0.1"}

    config_path.write_text('{"webserver_host": "example.com"}')
    run(tmp_path)
    assert json.loads(config_path.read_text())["webserver_host"] == "example.com"
