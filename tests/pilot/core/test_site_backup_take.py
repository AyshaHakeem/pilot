from pathlib import Path
from unittest.mock import patch

from pilot.core.bench import Bench
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
