from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.database import make_database, site_database_name
from pilot.exceptions import CommandError
from pilot.utils import run_command

_SYSTEM_SCHEMAS = {"mysql", "performance_schema", "information_schema", "sys"}

_SCHEMA_SIZE_QUERY = (
    "SELECT table_schema, COALESCE(SUM(data_length + index_length), 0) "
    "FROM information_schema.TABLES GROUP BY table_schema"
)
_PG_DATABASE_SIZE_QUERY = (
    "SELECT datname, pg_database_size(datname) FROM pg_database WHERE datistemplate = false"
)


@dataclass
class DatabaseRow:
    schema: str
    site: str | None
    system: bool
    bytes: int


@dataclass
class DatabaseBreakdown:
    engine: str
    supported: bool
    used_bytes: int
    binlog_bytes: int
    core_bytes: int
    error_log_bytes: int = 0
    slow_log_bytes: int = 0
    binlog_index_bytes: int = 0
    databases: list[DatabaseRow] = field(default_factory=list)


@dataclass
class StorageItem:
    name: str
    bytes: int


@dataclass
class SiteStorage:
    name: str
    bytes: int
    private_files_bytes: int
    public_files_bytes: int
    backups_bytes: int
    other_bytes: int
    backup_files: list[StorageItem] = field(default_factory=list)
    other_entries: list[StorageItem] = field(default_factory=list)


@dataclass
class BenchBreakdown:
    used_bytes: int
    apps: list[StorageItem]
    apps_bytes: int
    sites: list[SiteStorage]
    sites_bytes: int
    logs_bytes: int


@dataclass
class StorageBreakdown:
    disk_total: int
    disk_used: int
    database: DatabaseBreakdown
    bench: BenchBreakdown


@lru_cache(maxsize=256)
def directory_size_bytes(path: str) -> int:
    try:
        return int(run_command(["du", "-sb", path], timeout=10).stdout.split()[0])
    except (OSError, CommandError, IndexError, ValueError):
        return 0


_PRIVATE_SUBDIRS_EXCLUDED_FROM_OTHER = {"files", "backups"}


def _storage_entry(path: Path) -> StorageItem:
    size = path.stat().st_size if path.is_file() else directory_size_bytes(str(path))
    return StorageItem(name=path.name, bytes=size)


def _site_backup_files(site_path: Path) -> list[StorageItem]:
    backups_dir = site_path / "private" / "backups"
    if not backups_dir.is_dir():
        return []
    return [_storage_entry(child) for child in backups_dir.iterdir()]


def _site_other_entries(site_path: Path) -> list[StorageItem]:
    entries: list[StorageItem] = []
    for child in site_path.iterdir():
        if child.name == "public":
            continue
        if child.name == "private":
            entries.extend(
                _storage_entry(grandchild)
                for grandchild in child.iterdir()
                if grandchild.name not in _PRIVATE_SUBDIRS_EXCLUDED_FROM_OTHER
            )
            continue
        entries.append(_storage_entry(child))
    return entries


def _mariadb_variable_file_size(database, variable: str, data_dir: Path) -> int:
    """Size of the file a MariaDB system variable (e.g. @@log_error) points at."""
    value = str(database.get_scalar(f"SELECT {variable}") or "")
    if not value:
        return 0
    path = Path(value)
    if not path.is_absolute():
        path = data_dir / path
    try:
        return path.stat().st_size
    except OSError:
        return 0


class StorageProvider:
    """Disk usage breakdown for a bench's database engine and bench directories."""

    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root
        self._config = BenchConfig.read(bench_root, validate=False)
        self._bench = Bench(self._config, bench_root)

    def get_breakdown(self, disk_total: int, disk_used: int) -> StorageBreakdown:
        return StorageBreakdown(
            disk_total=disk_total,
            disk_used=disk_used,
            database=self._database_breakdown(),
            bench=self._bench_breakdown(),
        )

    def _database_breakdown(self) -> DatabaseBreakdown:
        engine = self._config.db_type
        if engine == "mariadb":
            return self._mariadb_breakdown()
        if engine == "postgres":
            return self._postgres_breakdown()
        return DatabaseBreakdown(
            engine=engine, supported=False, used_bytes=0, binlog_bytes=0, core_bytes=0
        )

    def _mariadb_breakdown(self) -> DatabaseBreakdown:
        from pilot.managers.database.mariadb import MariaDBManager

        data_dir = MariaDBManager(self._config.mariadb).data_dir
        total_bytes = directory_size_bytes(str(data_dir))
        database = make_database(self._config)
        databases = self._schema_sizes(database, _SCHEMA_SIZE_QUERY)
        binlog_bytes = database.get_binlog_status().size_bytes
        error_log_bytes = _mariadb_variable_file_size(database, "@@log_error", data_dir)
        slow_log_bytes = _mariadb_variable_file_size(database, "@@slow_query_log_file", data_dir)
        binlog_index_bytes = _mariadb_variable_file_size(database, "@@log_bin_index", data_dir)
        schema_bytes = sum(row.bytes for row in databases)
        core_bytes = max(
            total_bytes
            - binlog_bytes
            - schema_bytes
            - error_log_bytes
            - slow_log_bytes
            - binlog_index_bytes,
            0,
        )
        return DatabaseBreakdown(
            engine="mariadb",
            supported=True,
            used_bytes=total_bytes,
            binlog_bytes=binlog_bytes,
            core_bytes=core_bytes,
            error_log_bytes=error_log_bytes,
            slow_log_bytes=slow_log_bytes,
            binlog_index_bytes=binlog_index_bytes,
            databases=databases,
        )

    def _postgres_breakdown(self) -> DatabaseBreakdown:
        database = make_database(self._config)
        databases = self._schema_sizes(database, _PG_DATABASE_SIZE_QUERY)
        used_bytes = sum(row.bytes for row in databases)
        return DatabaseBreakdown(
            engine="postgres",
            supported=True,
            used_bytes=used_bytes,
            binlog_bytes=0,
            core_bytes=0,
            databases=databases,
        )

    def _schema_sizes(self, database, query: str) -> list[DatabaseRow]:
        site_by_db = self._site_by_database_name()
        result = database.execute(query)
        return [
            DatabaseRow(
                schema=row[0],
                site=site_by_db.get(row[0]),
                system=row[0] in _SYSTEM_SCHEMAS,
                bytes=int(row[1]),
            )
            for row in result.rows
            if row[0] is not None
        ]

    def _site_by_database_name(self) -> dict[str, str]:
        mapping = {}
        for site in self._bench.sites():
            try:
                db_name = site_database_name(self._bench_root, site.config.name)
            except (FileNotFoundError, ValueError):
                continue
            if db_name:
                mapping[db_name] = site.config.name
        return mapping

    def _bench_breakdown(self) -> BenchBreakdown:
        apps = [
            StorageItem(name=app.config.name, bytes=directory_size_bytes(str(app.path)))
            for app in self._bench.apps()
        ]
        sites = [self._site_storage(site) for site in self._bench.sites()]
        logs_bytes = directory_size_bytes(str(self._bench.logs_path))
        apps_bytes = sum(item.bytes for item in apps)
        sites_bytes = sum(item.bytes for item in sites)
        return BenchBreakdown(
            used_bytes=apps_bytes + sites_bytes + logs_bytes,
            apps=apps,
            apps_bytes=apps_bytes,
            sites=sites,
            sites_bytes=sites_bytes,
            logs_bytes=logs_bytes,
        )

    def _site_storage(self, site) -> SiteStorage:
        total_bytes = directory_size_bytes(str(site.path))
        private_files_bytes = directory_size_bytes(str(site.path / "private" / "files"))
        public_files_bytes = directory_size_bytes(str(site.path / "public"))
        backups_bytes = directory_size_bytes(str(site.path / "private" / "backups"))
        other_bytes = max(
            total_bytes - private_files_bytes - public_files_bytes - backups_bytes, 0
        )
        return SiteStorage(
            name=site.config.name,
            bytes=total_bytes,
            private_files_bytes=private_files_bytes,
            public_files_bytes=public_files_bytes,
            backups_bytes=backups_bytes,
            other_bytes=other_bytes,
            backup_files=_site_backup_files(site.path),
            other_entries=_site_other_entries(site.path),
        )
