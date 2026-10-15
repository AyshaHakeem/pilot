from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.bench import Bench
from pilot.exceptions import BenchError
from tests.pilot.commands.test_commands import make_bench


def _site_with_backups(tmp_path: Path) -> tuple[Bench, Path]:
    bench = make_bench(tmp_path)
    backups = bench.sites_path / "site1.localhost" / "backups"
    backups.mkdir(parents=True)
    return bench, backups


def test_a_backup_returns_its_own_run_while_another_one_is_written(tmp_path: Path) -> None:
    bench, backups = _site_with_backups(tmp_path)
    # A scheduled backup started later and is still writing its newer run.
    (backups / "20261015_030000-site1-database.sql.gz").write_text("half written")

    def frappe_backup(argv, **kwargs):
        staging = Path(argv[argv.index("--backup-path") + 1])
        (staging / "20261015_020000-site1-database.sql.gz").write_text("complete")
        (staging / "20261015_020000-site1-files.tar").write_text("complete")

    with patch("pilot.utils.run_command", side_effect=frappe_backup):
        timestamp, files = bench.site("site1.localhost").backups.take(with_files=True)

    assert timestamp == "20261015_020000"
    assert sorted(path.name for path in files) == [
        "20261015_020000-site1-database.sql.gz",
        "20261015_020000-site1-files.tar",
    ]
    assert all(path.parent == backups for path in files)
    assert sorted(path.name for path in backups.iterdir()) == [
        "20261015_020000-site1-database.sql.gz",
        "20261015_020000-site1-files.tar",
        "20261015_030000-site1-database.sql.gz",
    ]


def test_a_backup_never_overwrites_a_run_from_the_same_second(tmp_path: Path) -> None:
    bench, backups = _site_with_backups(tmp_path)
    (backups / "20261015_020000-site1-database.sql.gz").write_text("other run")

    def frappe_backup(argv, **kwargs):
        staging = Path(argv[argv.index("--backup-path") + 1])
        (staging / "20261015_020000-site1-database.sql.gz").write_text("this run")

    with (
        patch("pilot.utils.run_command", side_effect=frappe_backup),
        pytest.raises(BenchError, match="same second"),
    ):
        bench.site("site1.localhost").backups.take(with_files=False)

    assert (backups / "20261015_020000-site1-database.sql.gz").read_text() == "other run"
    assert [path.name for path in backups.iterdir()] == ["20261015_020000-site1-database.sql.gz"]


def test_retention_waits_while_a_run_is_published(tmp_path: Path) -> None:
    from pilot.internal.atomic_file import exclusive_file_lock

    bench, _ = _site_with_backups(tmp_path)
    site_backups = bench.site("site1.localhost").backups
    published: list[str] = []

    def lock(path):
        assert path == site_backups.runs_lock_path
        published.append("locked")
        return exclusive_file_lock(path)

    with patch("pilot.core.site.backups.exclusive_file_lock", side_effect=lock):
        site_backups._publish([])
        with patch("pilot.core.site.backups.read_retention", return_value=object()), patch(
            "pilot.core.site.backups.BackupRetentionPolicy"
        ):
            site_backups.prune()

    assert published == ["locked", "locked"]
