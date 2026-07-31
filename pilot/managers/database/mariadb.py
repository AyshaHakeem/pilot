import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

from pilot.config import MariaDBConfig
from pilot.core.mariadb_memory import MariaDBMemorySizing, calculate_mariadb_memory
from pilot.exceptions import DatabaseError
from pilot.managers.database.base import UserOwnedDBManager
from pilot.managers.platform import is_macos, which
from pilot.utils import cli_root, run_command

_CLIENT_TIMEOUT = 5


class MariaDBManager(UserOwnedDBManager):
    _UNIT_NAME = "pilot-mariadb.service"
    _DISPLAY_NAME = "MariaDB"
    _SYSTEM_PACKAGE = "mariadb-server"
    _BREW_FORMULA_BASE = "mariadb"
    _DEFAULT_VERSION = "11.8"

    def __init__(self, config: MariaDBConfig) -> None:
        self.config = config

    @property
    def state_dir(self) -> Path:
        """One rootless MariaDB server per host, shared by all its benches."""
        return cli_root() / "databases" / "mariadb"

    @property
    def config_dir(self) -> Path:
        return self.state_dir / "config"

    @property
    def my_cnf_path(self) -> Path:
        return self.config_dir / "my.cnf"

    @property
    def data_dir(self) -> Path:
        return self.state_dir / "data"

    @property
    def run_dir(self) -> Path:
        return self.state_dir / "run"

    @property
    def pid_file(self) -> Path:
        return self.run_dir / "mysqld.pid"

    @property
    def socket_path(self) -> str:
        if self.config.socket_path:
            return self.config.socket_path
        return str(self.run_dir / "mysqld.sock")

    def is_installed(self) -> bool:
        # which() searches sbin too; mysqld/mariadbd live in /usr/sbin.
        return bool(which("mysqld") or which("mariadbd"))

    def is_provisioned(self) -> bool:
        if is_macos():
            return self.is_running() and not self.is_unsecured()
        return super().is_provisioned() and self.my_cnf_path.exists()

    def _provision_macos(self):
        if not self.is_running():
            self.start()
        self._wait_until_reachable()
        self.secure_installation()

    def provision(self) -> None:
        """Install, start and secure the shared MariaDB server."""
        self.install()
        if is_macos():
            return self._provision_macos()

        if not self.is_provisioned():
            sizing = self._write_config()
            self._initialize_data_dir()
            self._install_unit(sizing)
            self._reset_failed_state()
            run_command(self._systemctl("enable", "--now", self._UNIT_NAME), env=self._systemctl_env())

        elif not self.is_running():
            self._reset_failed_state()
            run_command(self._systemctl("start", self._UNIT_NAME), env=self._systemctl_env())

        self._wait_until_reachable()
        self.secure_installation()
        return None

    def _initialize_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        run_command(
            [
                "mariadb-install-db",
                f"--defaults-file={self.my_cnf_path}",
                f"--datadir={self.data_dir}",
                "--skip-test-db",
            ]
        )

    def _write_config(self) -> MariaDBMemorySizing:
        """Write every server setting into our own my.cnf so mariadbd, launched
        with --defaults-file, never falls back to reading /etc/mysql/my.cnf."""
        sizing = calculate_mariadb_memory(self._total_memory_mb())
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        content = (
            "# Managed by Pilot.\n"
            "[mysqld]\n"
            f"datadir = {self.data_dir}\n"
            f"socket = {self.socket_path}\n"
            f"port = {self.config.port}\n"
            f"pid-file = {self.pid_file}\n"
            "bind-address = 127.0.0.1\n"
            "\n"
            "default-storage-engine = InnoDB\n"
            "character-set-server = utf8mb4\n"
            "collation-server = utf8mb4_unicode_ci\n"
            "character-set-client-handshake = OFF\n"
            "local-infile = OFF\n"
            "max-allowed-packet = 512M\n"
            "\n"
            f"innodb-buffer-pool-size = {sizing.innodb_buffer_pool_mb}M\n"
            f"innodb-log-file-size = {sizing.innodb_log_file_mb}M\n"
            "innodb-flush-log-at-trx-commit = 1\n"
            "innodb-print-all-deadlocks = ON\n"
            "innodb-stats-persistent-sample-pages = 256\n"
            "innodb-strict-mode = ON\n"
            "innodb-snapshot-isolation = OFF\n"
            f"key-buffer-size = {sizing.key_buffer_mb}M\n"
            f"max-connections = {sizing.max_connections}\n"
            "slave-connections-needed-for-purge = 0\n"
            "tmp-table-size = 32M\n"
            "max-heap-table-size = 32M\n"
            "\n"
            "[client]\n"
            f"socket = {self.socket_path}\n"
            f"port = {self.config.port}\n"
            "default-character-set = utf8mb4\n"
            "\n"
            "[mariadb-dump]\n"
            "max-allowed-packet = 512M\n"
        )
        self.my_cnf_path.write_text(content, encoding="utf-8")
        self.my_cnf_path.chmod(0o600)
        return sizing

    def _total_memory_mb(self) -> int:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError) as exc:
            raise DatabaseError("Could not detect total system memory for MariaDB sizing.") from exc
        total_memory_mb = pages * page_size // (1024 * 1024)
        if total_memory_mb <= 0:
            raise DatabaseError("Could not detect total system memory for MariaDB sizing.")
        return total_memory_mb

    def _install_unit(self, sizing: MariaDBMemorySizing) -> None:
        mariadbd = which("mariadbd") or which("mysqld") or "/usr/sbin/mariadbd"
        content = (
            "[Unit]\n"
            "Description=MariaDB (pilot, user-owned)\n\n"
            "[Service]\n"
            "Type=simple\n"
            # --defaults-file must be the first argument; it makes mariadbd
            # skip every system default file instead of layering over them.
            f"ExecStart={mariadbd} --defaults-file={self.my_cnf_path}\n"
            "LimitNOFILE=65535\n"
            f"MemoryHigh={sizing.memory_high_mb}M\n"
            f"MemoryMax={sizing.memory_max_mb}M\n"
            "MemorySwapMax=100M\n"
            "Restart=on-failure\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        unit_dir = self.user_unit_dir
        unit_dir.mkdir(parents=True, exist_ok=True)
        self.unit_path.write_text(content)
        run_command(self._systemctl("daemon-reload"), env=self._systemctl_env())

    def is_reachable(self) -> bool:
        if not self.is_running():
            return False
        if is_macos():
            # Homebrew owns the socket location here, not socket_path() (our
            # own state_dir, only ever created for the Linux systemd unit) -
            # is_running() is the only signal we have.
            return True
        return Path(self.socket_path).exists()

    def is_unsecured(self) -> bool:
        """True when the admin account is still reachable without a password."""
        cmd = ["mariadb"]
        if not is_macos():
            cmd.append(f"--socket={self.socket_path}")
        cmd += ["-u", self.config.admin_user, "--batch", "--skip-column-names", "-e", "SELECT 1"]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_CLIENT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def has_valid_credentials(self, password: str | None = None) -> bool:
        """Check admin credentials using MYSQL_PWD, never argv."""
        pw = self.config.root_password if password is None else password
        cmd = [
            "mariadb",
            f"--connect-timeout={_CLIENT_TIMEOUT}",
            "-u",
            self.config.admin_user,
            "--batch",
            "--skip-column-names",
        ]
        socket_path = self._detect_socket()
        if socket_path:
            cmd.append(f"--socket={socket_path}")
        else:
            cmd += ["-h", self.config.host, "-P", str(self.config.port)]
        cmd += ["-e", "SELECT 1"]
        try:
            result = subprocess.run(
                cmd,
                env={**os.environ, "MYSQL_PWD": pw},
                capture_output=True,
                text=True,
                timeout=_CLIENT_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    def secure_installation(self) -> None:
        """Create/update the admin account and apply basic hardening."""
        if self.has_valid_credentials():
            return
        # admin_user can come from setup wizard input; quote it like a value.
        user = self._sql_quote(self.config.admin_user)
        password = self._sql_quote(self.config.root_password)
        statements = [
            # Covers fresh installs and siblings that secured a different password.
            f"CREATE USER IF NOT EXISTS {user}@'localhost' IDENTIFIED BY {password};",
            f"ALTER USER {user}@'localhost' IDENTIFIED BY {password};",
            f"GRANT ALL PRIVILEGES ON *.* TO {user}@'localhost' WITH GRANT OPTION;",
            "DROP USER IF EXISTS ''@'localhost';",
            "DROP USER IF EXISTS ''@'%';",
            "DROP DATABASE IF EXISTS test;",
            "FLUSH PRIVILEGES;",
        ]
        self._run_sql_as_superuser("\n".join(statements))

    def _run_sql_as_superuser(self, sql: str) -> None:
        cmd = ["mariadb"]
        if not is_macos():
            # mariadb-install-db grants this OS user unix_socket admin access.
            cmd.append(f"--socket={self.socket_path}")
        # macOS uses Homebrew's default socket, not our Linux state dir.
        subprocess.run(cmd, input=sql, text=True, check=True)

    @staticmethod
    def _sql_quote(value: str) -> str:
        """Quote a value as a MariaDB string literal (escaping \\ and ')."""
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    @contextmanager
    def snapshot_lock(self):
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("FLUSH TABLES WITH READ LOCK")
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("UNLOCK TABLES")
            connection.close()

    def connect(self, password: str | None = None, cursorclass=None):
        """Open an admin connection to the shared MariaDB server."""
        import pymysql

        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.admin_user,
            password=self.config.root_password if password is None else password,
            unix_socket=self._detect_socket() or None,
            cursorclass=cursorclass or pymysql.cursors.Cursor,
        )

    def _detect_socket(self) -> str:
        if self.config.socket_path:
            return self.config.socket_path
        if not is_macos() and Path(self.socket_path).exists():
            return self.socket_path
        return ""
