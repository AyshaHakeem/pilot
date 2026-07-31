from __future__ import annotations

from pathlib import Path

from pilot.config.common import CommonConfig
from pilot.config.letsencrypt import LetsEncryptConfig
from pilot.config.mariadb import MariaDBConfig
from pilot.config.postgres import PostgresConfig


def test_path_resolves_next_to_benches_root(tmp_path: Path) -> None:
    assert CommonConfig.path(tmp_path) == tmp_path / "common_config.toml"


def test_read_missing_file_returns_defaults(tmp_path: Path) -> None:
    assert CommonConfig.read(tmp_path) == CommonConfig()


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    config = CommonConfig(
        mariadb=MariaDBConfig(host="db.internal", port=3307, root_password="s3cret", admin_user="root"),
        postgres=PostgresConfig(host="pg.internal", port=5433, root_password="pgsecret"),
        letsencrypt=LetsEncryptConfig(email="ops@example.com"),
        jwks_url="https://issuer.example.com/jwks.json",
        jwks_audience="bench-fleet",
    )
    config.write(tmp_path)
    assert CommonConfig.read(tmp_path) == config


def test_read_ignores_stale_mariadb_instance_keys(tmp_path: Path) -> None:
    """Legacy MariaDB instance keys (from an older Pilot schema) are
    ignored, not rejected, when read back."""
    (tmp_path / "common_config.toml").write_text(
        '[mariadb]\nroot_password = "root"\ninstance = "old-bench"\n'
        'version = "10.6"\ndata_dir = "/var/lib/mysql-old-bench"\n'
    )
    config = CommonConfig.read(tmp_path)
    assert not hasattr(config.mariadb, "instance")
    assert config.mariadb.root_password == "root"


def test_read_ignores_stale_postgres_instance_keys(tmp_path: Path) -> None:
    (tmp_path / "common_config.toml").write_text(
        '[postgres]\nroot_password = "secret"\ninstance = "old-bench"\nversion = "15"\n'
    )
    config = CommonConfig.read(tmp_path)
    assert not hasattr(config.postgres, "instance")
    assert config.postgres.root_password == "secret"


def test_jwks_omitted_from_output_when_unset(tmp_path: Path) -> None:
    CommonConfig().write(tmp_path)
    assert "[admin]" not in CommonConfig.path(tmp_path).read_text()
