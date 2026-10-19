from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pilot.config import BackupConfig
from pilot.config.site_backup import read_retention, write_retention
from pilot.patches.enroll_sites_in_scheduled_backups import run


def _bench(tmp_path: Path, *sites: str) -> Path:
    bench = tmp_path / "benches" / "one"
    (bench / "sites").mkdir(parents=True)
    for site in sites:
        (bench / "sites" / site).mkdir(parents=True)
        (bench / "sites" / site / "site_config.json").write_text(json.dumps({"db_name": site}))
    (bench / "bench.toml").write_text('[bench]\nname = "one"\npython = "3.14"\n')
    return bench


def test_sites_without_a_policy_are_enrolled_once_and_custom_ones_kept(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "a.localhost", "custom.localhost")
    custom = bench / "sites" / "custom.localhost" / "site_config.json"
    write_retention(custom, BackupConfig(scheme="fifo", keep_last=3))

    with patch("pilot.core.bench.backups.BenchBackups.install_schedule") as install:
        run(tmp_path / "benches")
        run(tmp_path / "benches")

    install.assert_called_once()
    assert read_retention(bench / "sites" / "a.localhost" / "site_config.json").calendar_rotation is True
    assert read_retention(custom) == BackupConfig(scheme="fifo", keep_last=3)


def test_a_bench_without_sites_gets_no_schedule(tmp_path: Path) -> None:
    _bench(tmp_path)

    with patch("pilot.core.bench.backups.BenchBackups.install_schedule") as install:
        run(tmp_path / "benches")

    install.assert_not_called()
