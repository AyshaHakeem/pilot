from __future__ import annotations

import stat
import subprocess
from unittest.mock import PropertyMock, patch

import pytest

from pilot.config import MariaDBConfig
from pilot.core.mariadb_memory import calculate_mariadb_memory
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager

MODULE = "pilot.managers.database.mariadb"
BASE_MODULE = "pilot.managers.database.base"


def _manager(password: str = "root") -> MariaDBManager:
    return MariaDBManager(MariaDBConfig(root_password=password))


def test_socket_path_defaults_under_state_dir() -> None:
    assert _manager().socket_path.endswith("/databases/mariadb/run/mysqld.sock")


def test_socket_path_honors_explicit_value() -> None:
    assert MariaDBManager(MariaDBConfig(socket_path="/tmp/custom.sock")).socket_path == "/tmp/custom.sock"


def test_default_port_avoids_standard_mysql_port() -> None:
    assert MariaDBConfig().port == 3310


def test_press_unified_memory_sizing_is_adapted_for_pilot() -> None:
    sizing = calculate_mariadb_memory(8192)

    assert sizing.mariadb_memory_mb == 3172
    assert sizing.innodb_buffer_pool_mb == 1692
    assert sizing.max_connections == 50
    assert sizing.key_buffer_mb == 32
    assert sizing.innodb_log_file_mb == 512
    assert sizing.memory_high_mb == 2148
    assert sizing.memory_max_mb == 3172


def test_small_vm_limits_leave_memory_for_other_pilot_processes() -> None:
    sizing = calculate_mariadb_memory(2048)

    assert sizing.innodb_buffer_pool_mb == 128
    assert sizing.max_connections == 50
    assert sizing.innodb_log_file_mb == 48
    assert sizing.memory_high_mb == 384
    assert sizing.memory_max_mb == 512
    assert sizing.memory_max_mb < sizing.total_memory_mb


@pytest.mark.parametrize("total_memory_mb", [256, 512, 1024, 2048, 8192])
def test_memory_limits_never_claim_more_than_half_the_host(total_memory_mb: int) -> None:
    sizing = calculate_mariadb_memory(total_memory_mb)

    assert 0 < sizing.memory_high_mb <= sizing.memory_max_mb
    assert sizing.memory_max_mb <= total_memory_mb // 2


@pytest.mark.parametrize(
    ("total_memory_mb", "expected_log_file_mb"),
    [(4096, 128), (8192, 512), (16384, 1024), (32768, 2048)],
)
def test_log_file_size_uses_press_memory_bands(
    total_memory_mb: int,
    expected_log_file_mb: int,
) -> None:
    assert calculate_mariadb_memory(total_memory_mb).innodb_log_file_mb == expected_log_file_mb


def test_existing_defaults_to_false() -> None:
    assert MariaDBConfig().existing is False


def test_existing_is_not_inferred_from_host() -> None:
    assert MariaDBConfig(host="db.example.com").existing is False


def test_install_raises_when_missing_on_linux() -> None:
    m = _manager()
    with (
        patch.object(m, "is_installed", return_value=False),
        patch(f"{BASE_MODULE}.is_macos", return_value=False),
        pytest.raises(DatabaseError, match=r"install\.sh"),
    ):
        m.install()


def test_start_targets_systemctl_user_on_linux() -> None:
    m = _manager()
    with (
        patch(f"{BASE_MODULE}.is_macos", return_value=False),
        patch(f"{BASE_MODULE}.run_command") as rc,
    ):
        m.start()
    assert rc.call_args.args[0] == ["systemctl", "--user", "start", "pilot-mariadb.service"]


def test_provision_initialises_and_installs_unit_when_fresh(tmp_path) -> None:
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(m, "install"),
        patch.object(type(m), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(m, "is_provisioned", return_value=False),
        patch.object(m, "is_running", return_value=False),
        patch.object(m, "_total_memory_mb", return_value=8192),
        patch.object(m, "_install_unit") as install_unit,
        patch.object(m, "_reset_failed_state") as reset_failed_state,
        patch.object(m, "_wait_until_reachable"),
        patch.object(m, "secure_installation") as secure,
        patch(f"{MODULE}.run_command") as rc,
    ):
        m.provision()
        assert m.my_cnf_path.read_text().startswith("# Managed by Pilot.\n[mysqld]\n")
    install_unit.assert_called_once()
    reset_failed_state.assert_called_once()
    secure.assert_called_once()
    argv_calls = [c.args[0] for c in rc.call_args_list]
    assert any("mariadb-install-db" in argv for argv in argv_calls)


def test_macos_provision_does_not_write_linux_configuration() -> None:
    manager = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch.object(manager, "install"),
        patch.object(manager, "_provision_macos") as provision_macos,
        patch.object(manager, "_write_config") as write_config,
    ):
        manager.provision()

    provision_macos.assert_called_once()
    write_config.assert_not_called()


def test_write_config_contains_all_server_settings(tmp_path) -> None:
    manager = MariaDBManager(MariaDBConfig(port=4306))
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(manager, "_total_memory_mb", return_value=8192),
    ):
        sizing = manager._write_config()

    option_file = tmp_path / "config" / "my.cnf"
    content = option_file.read_text()
    assert stat.S_IMODE(option_file.stat().st_mode) == 0o600
    assert f"datadir = {tmp_path / 'data'}" in content
    assert f"socket = {tmp_path / 'run' / 'mysqld.sock'}" in content
    assert f"pid-file = {tmp_path / 'run' / 'mysqld.pid'}" in content
    assert "bind-address = 127.0.0.1" in content
    assert "port = 4306" in content
    assert "character-set-server = utf8mb4" in content
    assert "collation-server = utf8mb4_unicode_ci" in content
    assert "local-infile = OFF" in content
    assert "innodb-stats-persistent-sample-pages = 256" in content
    assert "innodb-snapshot-isolation = OFF" in content
    assert "slave-connections-needed-for-purge = 0" in content
    assert f"innodb-buffer-pool-size = {sizing.innodb_buffer_pool_mb}M" in content
    assert f"innodb-log-file-size = {sizing.innodb_log_file_mb}M" in content
    assert f"key-buffer-size = {sizing.key_buffer_mb}M" in content
    assert f"max-connections = {sizing.max_connections}" in content

    for unsupported_or_unmanaged_option in (
        "innodb-file-format",
        "innodb-file-per-table",
        "innodb-flush-method",
        "innodb-large-prefix",
        "log-bin",
        "query-cache",
        "slow-query-log",
    ):
        assert unsupported_or_unmanaged_option not in content


def test_data_directory_initialization_uses_pilot_option_file(tmp_path) -> None:
    manager = _manager()
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.run_command") as run,
    ):
        manager._initialize_data_dir()

    assert run.call_args.args[0] == [
        "mariadb-install-db",
        f"--defaults-file={tmp_path / 'config' / 'my.cnf'}",
        f"--datadir={tmp_path / 'data'}",
        "--skip-test-db",
    ]


def test_linux_unit_starts_with_option_file_and_memory_limits(tmp_path) -> None:
    manager = _manager()
    sizing = calculate_mariadb_memory(8192)
    unit_dir = tmp_path / "units"
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(type(manager), "user_unit_dir", new_callable=PropertyMock, return_value=unit_dir),
        patch(f"{MODULE}.which", return_value="/usr/sbin/mariadbd"),
        patch(f"{MODULE}.run_command"),
    ):
        manager._install_unit(sizing)

    content = (unit_dir / "pilot-mariadb.service").read_text()
    assert f"ExecStart=/usr/sbin/mariadbd --defaults-file={tmp_path / 'config' / 'my.cnf'}" in content
    assert "LimitNOFILE=65535" in content
    assert f"MemoryHigh={sizing.memory_high_mb}M" in content
    assert f"MemoryMax={sizing.memory_max_mb}M" in content
    assert "MemorySwapMax=100M" in content


def test_is_provisioned_on_macos_checks_live_server_not_a_marker_file() -> None:
    """macOS provisioning state comes from the live secured server."""
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch.object(m, "is_running", return_value=False),
    ):
        assert m.is_provisioned() is False  # not running yet
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch.object(m, "is_running", return_value=True),
        patch.object(m, "is_unsecured", return_value=True),
    ):
        assert m.is_provisioned() is False  # up but still passwordless
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch.object(m, "is_running", return_value=True),
        patch.object(m, "is_unsecured", return_value=False),
    ):
        assert m.is_provisioned() is True  # up and already secured


def test_is_provisioned_false_when_data_dir_wiped_but_unit_still_exists(tmp_path) -> None:
    """A stale systemd unit outliving a deleted state dir must not look provisioned."""
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(type(m), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{BASE_MODULE}.UserOwnedDBManager.is_provisioned", return_value=True),
    ):
        assert m.is_provisioned() is False


def test_wait_until_reachable_raises_after_timeout() -> None:
    m = _manager()
    with (
        patch.object(m, "is_reachable", return_value=False),
        patch(f"{BASE_MODULE}.time.sleep"),
        pytest.raises(DatabaseError, match="did not become reachable"),
    ):
        m._wait_until_reachable(timeout=0.01)


def test_provision_resets_failed_state_before_restarting_stopped_unit() -> None:
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(m, "install"),
        patch.object(m, "is_provisioned", return_value=True),
        patch.object(m, "is_running", return_value=False),
        patch.object(m, "_wait_until_reachable"),
        patch.object(m, "secure_installation"),
        patch(f"{MODULE}.run_command") as rc,
        patch(f"{BASE_MODULE}.subprocess.run") as reset_run,
    ):
        m.provision()
    reset_run.assert_called_once()
    assert reset_run.call_args.args[0] == ["systemctl", "--user", "reset-failed", "pilot-mariadb.service"]
    assert rc.call_args.args[0] == ["systemctl", "--user", "start", "pilot-mariadb.service"]


def test_provision_reuses_already_provisioned_server() -> None:
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(m, "install"),
        patch.object(m, "is_provisioned", return_value=True),
        patch.object(m, "is_running", return_value=True),
        patch.object(m, "_write_config") as write_config,
        patch.object(m, "_install_unit") as install_unit,
        patch.object(m, "_wait_until_reachable"),
        patch.object(m, "secure_installation") as secure,
        patch(f"{MODULE}.run_command") as rc,
    ):
        m.provision()
    install_unit.assert_not_called()
    write_config.assert_not_called()
    rc.assert_not_called()
    secure.assert_called_once()


def test_sql_quote_plain() -> None:
    assert MariaDBManager._sql_quote("hunter2") == "'hunter2'"


def test_sql_quote_escapes_single_quote() -> None:
    assert MariaDBManager._sql_quote("a'b") == "'a\\'b'"


def test_sql_quote_escapes_backslash() -> None:
    assert MariaDBManager._sql_quote("a\\b") == "'a\\\\b'"


def test_secure_installation_noop_when_credentials_valid() -> None:
    manager = _manager()
    with (
        patch.object(manager, "has_valid_credentials", return_value=True),
        patch.object(manager, "_run_sql_as_superuser") as run_sql,
    ):
        manager.secure_installation()
    run_sql.assert_not_called()


def test_secure_installation_creates_and_grants() -> None:
    manager = _manager("s3cret")
    with (
        patch.object(manager, "has_valid_credentials", return_value=False),
        patch.object(manager, "_run_sql_as_superuser") as run_sql,
    ):
        manager.secure_installation()
    run_sql.assert_called_once()
    sql = run_sql.call_args[0][0]
    assert "CREATE USER IF NOT EXISTS 'root'@'localhost' IDENTIFIED BY 's3cret';" in sql
    assert "ALTER USER 'root'@'localhost' IDENTIFIED BY 's3cret';" in sql
    assert "GRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost' WITH GRANT OPTION;" in sql
    assert "DROP USER IF EXISTS ''@'localhost';" in sql
    assert "DROP DATABASE IF EXISTS test;" in sql
    assert "FLUSH PRIVILEGES;" in sql


def test_secure_installation_escapes_malicious_admin_user() -> None:
    """admin_user is SQL-quoted before secure-installation statements."""
    config = MariaDBConfig(root_password="pw", admin_user="root'; DROP TABLE mysql.user; --")
    manager = MariaDBManager(config)
    with (
        patch.object(manager, "has_valid_credentials", return_value=False),
        patch.object(manager, "_run_sql_as_superuser") as run_sql,
    ):
        manager.secure_installation()
    sql = run_sql.call_args[0][0]
    # The attacker's quote must be escaped, not break out of the string literal.
    assert "root\\'; DROP TABLE mysql.user; --" in sql
    assert "CREATE USER IF NOT EXISTS 'root'" not in sql


def test_run_sql_as_superuser_no_sudo() -> None:
    m = _manager()
    with patch(f"{MODULE}.is_macos", return_value=False), patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert "sudo" not in cmd
    assert cmd[0] == "mariadb"


def test_is_reachable_on_macos_ignores_local_socket_path() -> None:
    """socket_path() (our own _STATE_DIR) is never created on macOS - only
    is_running() is a meaningful signal there."""
    m = _manager()
    with (
        patch.object(m, "is_running", return_value=True),
        patch(f"{MODULE}.is_macos", return_value=True),
    ):
        assert m.is_reachable() is True


def test_run_sql_as_superuser_omits_local_socket_on_macos() -> None:
    """Homebrew's mariadb client owns socket resolution on macOS -
    socket_path() (our own _STATE_DIR) is never created there."""
    m = _manager()
    with patch(f"{MODULE}.is_macos", return_value=True), patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert cmd == ["mariadb"]


def test_has_valid_credentials_true_on_successful_connect() -> None:
    manager = _manager()
    with patch(f"{MODULE}.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0)
        assert manager.has_valid_credentials("pw") is True
    # Password is passed via MYSQL_PWD env, never argv.
    args, kwargs = run.call_args
    assert "pw" not in args[0]
    assert kwargs["env"]["MYSQL_PWD"] == "pw"


def test_has_valid_credentials_false_on_error() -> None:
    manager = _manager()
    with patch(f"{MODULE}.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 1)
        assert manager.has_valid_credentials("wrong") is False


def test_has_valid_credentials_times_out() -> None:
    manager = _manager()
    with patch(
        f"{MODULE}.subprocess.run",
        side_effect=subprocess.TimeoutExpired("mariadb", 5),
    ):
        assert manager.has_valid_credentials("wrong") is False


def _client(tmp_path):
    from admin.backend.app import create_app

    app = create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def _post_validate(client, password: str):
    return client.post(
        "/api/v1/setup/database-validations",
        json={"engine": "mariadb", "password": password},
    )


def test_validate_endpoint_will_install_when_not_installed(tmp_path) -> None:
    with patch(f"{MODULE}.MariaDBManager.is_installed", return_value=False):
        resp = _post_validate(_client(tmp_path), "anything")
    assert resp.get_json() == {"engine": "mariadb", "state": "will_install"}


def test_validate_endpoint_will_install_when_not_provisioned(tmp_path) -> None:
    with (
        patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_provisioned", return_value=False),
    ):
        resp = _post_validate(_client(tmp_path), "anything")
    assert resp.get_json() == {"engine": "mariadb", "state": "will_install"}


def test_validate_endpoint_valid(tmp_path) -> None:
    with (
        patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_provisioned", return_value=True),
        patch(f"{MODULE}.MariaDBManager.has_valid_credentials", return_value=True),
    ):
        resp = _post_validate(_client(tmp_path), "correct")
    assert resp.get_json() == {"engine": "mariadb", "state": "valid"}


def test_validate_endpoint_invalid(tmp_path) -> None:
    with (
        patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_provisioned", return_value=True),
        patch(f"{MODULE}.MariaDBManager.has_valid_credentials", return_value=False),
    ):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"engine": "mariadb", "state": "invalid"}


def test_validate_endpoint_on_macos_checks_password_for_already_secured_server(tmp_path) -> None:
    """macOS validation checks credentials on an already-secured server."""
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_running", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_unsecured", return_value=False),
        patch(f"{MODULE}.MariaDBManager.has_valid_credentials", return_value=False),
    ):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"engine": "mariadb", "state": "invalid"}
