import gzip
from types import SimpleNamespace

import pytest

from pilot.config import SiteConfig
from pilot.core.site import Site
from pilot.core.site.backup_stream import SiteBackupStream
from pilot.exceptions import CommandError
from pilot.integrations.s3.base import S3IntegrationError

DUMP = b"CREATE TABLE tabUser;\n" * 50_000


class FakeOffsite:
    def __init__(self, failing: str = "") -> None:
        self.failing = failing
        self.uploaded: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def upload_stream(self, site_name, timestamp, filename, fileobj) -> None:
        data = b""
        while chunk := fileobj.read(64 * 1024):
            data += chunk
            if self.failing and filename.endswith(self.failing):
                raise S3IntegrationError(f"Failed to upload {filename}")
        self.uploaded[filename] = data

    def delete(self, site_name, timestamp, filename) -> None:
        self.deleted.append(filename)


def fake_frappe_backup(fail: bool = False):
    """Writes like Frappe: a gzip header in one session, then the dump appended in another."""

    def run(argv, **kwargs) -> None:
        paths = {flag: argv[index + 1] for index, flag in enumerate(argv) if flag.startswith("--backup-path")}
        with gzip.open(paths["--backup-path-db"], "wt") as header:
            header.write("-- begin frappe metadata\n")
        with open(paths["--backup-path-db"], "ab") as dump:
            dump.write(gzip.compress(DUMP[: len(DUMP) // 2] if fail else DUMP))
        if fail:
            raise CommandError("frappe backup failed", returncode=1)
        with open(paths["--backup-path-conf"], "w") as config:
            config.write("{}")
        for flag in ("--backup-path-files", "--backup-path-private-files"):
            with open(paths[flag], "wb") as tar:
                tar.write(b"tar" * 1000)

    return run


def site(tmp_path) -> Site:
    bench = SimpleNamespace(
        path=tmp_path, sites_path=tmp_path / "sites", frappe_call=["python", "-m", "frappe"]
    )
    return Site(SiteConfig(name="site1.local", apps=[]), bench)


def test_stream_uploads_every_file_whole_and_leaves_nothing_on_disk(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pilot.utils.run_command", fake_frappe_backup())
    offsite = FakeOffsite()

    timestamp, sizes = SiteBackupStream(site(tmp_path), offsite).run(with_files=True)

    database = f"{timestamp}-site1_local-database.sql.gz"
    assert gzip.decompress(offsite.uploaded[database]) == b"-- begin frappe metadata\n" + DUMP
    assert sorted(offsite.uploaded) == sorted(sizes)
    assert sizes == {name: len(data) for name, data in offsite.uploaded.items()}
    assert list((tmp_path / "sites" / "site1.local" / "backups").iterdir()) == []


@pytest.mark.parametrize(
    ("failing_backup", "failing_upload", "error"),
    [(True, "", CommandError), (False, "-files.tar", S3IntegrationError)],
)
def test_a_failed_run_leaves_nothing_offsite(
    tmp_path, monkeypatch, failing_backup, failing_upload, error
) -> None:
    """A cut-short backup never completes an upload; a failed upload drains its pipe and
    deletes the files that did upload."""
    monkeypatch.setattr("pilot.utils.run_command", fake_frappe_backup(fail=failing_backup))
    offsite = FakeOffsite(failing=failing_upload)

    with pytest.raises(error):
        SiteBackupStream(site(tmp_path), offsite).run(with_files=True)

    assert sorted(offsite.deleted) == sorted(offsite.uploaded)
