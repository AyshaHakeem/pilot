from __future__ import annotations

from dataclasses import dataclass

_HARDWARE_MEMORY_FACTOR = 0.972
_HARDWARE_MEMORY_RESERVE_MB = 218
_MARIADB_OPERATING_RESERVE_MB = 700
_MEMORY_PER_CONNECTION_MB = 35
_MIN_BUFFER_POOL_MB = 128
_MIN_MEMORY_MAX_MB = 512
_MIN_MEMORY_LIMIT_GAP_MB = 128
_MAX_HOST_MEMORY_SHARE = 0.5


@dataclass(frozen=True)
class MariaDBMemorySizing:
    """Startup values for MariaDB instance"""

    total_memory_mb: int
    mariadb_memory_mb: int
    innodb_buffer_pool_mb: int
    max_connections: int
    key_buffer_mb: int
    innodb_log_file_mb: int
    memory_high_mb: int
    memory_max_mb: int


def calculate_mariadb_memory(total_memory_mb: int) -> MariaDBMemorySizing:
    if total_memory_mb <= 0:
        raise ValueError("total_memory_mb must be greater than zero")

    real_memory_mb = _HARDWARE_MEMORY_FACTOR * total_memory_mb - _HARDWARE_MEMORY_RESERVE_MB
    mariadb_memory_mb = real_memory_mb / 2 - _MARIADB_OPERATING_RESERVE_MB

    recommended_connections = 5 * round(total_memory_mb / 1024)
    max_connections = max(50, recommended_connections)
    key_buffer_mb = 128 if mariadb_memory_mb > 4096 else 32
    base_memory_mb = key_buffer_mb + 32 + 16

    connection_aware_pool_mb = int(
        mariadb_memory_mb - base_memory_mb - recommended_connections * _MEMORY_PER_CONNECTION_MB
    )
    percentage_pool_mb = int(mariadb_memory_mb * 0.65)
    innodb_buffer_pool_mb = max(
        _MIN_BUFFER_POOL_MB,
        min(connection_aware_pool_mb, percentage_pool_mb),
    )

    # Pilot shares the VM with web, worker, and Redis processes. Keep the
    # startup floor, but never let it claim more than half of a small host.
    host_share_cap_mb = max(1, int(total_memory_mb * _MAX_HOST_MEMORY_SHARE))
    memory_max_mb = min(
        host_share_cap_mb,
        max(_MIN_MEMORY_MAX_MB, round(mariadb_memory_mb)),
    )
    memory_high_mb = max(
        1,
        min(
            memory_max_mb - _MIN_MEMORY_LIMIT_GAP_MB,
            round(max(mariadb_memory_mb - 1024, 1024)),
        ),
    )

    return MariaDBMemorySizing(
        total_memory_mb=total_memory_mb,
        mariadb_memory_mb=max(0, int(mariadb_memory_mb)),
        innodb_buffer_pool_mb=innodb_buffer_pool_mb,
        max_connections=max_connections,
        key_buffer_mb=key_buffer_mb,
        innodb_log_file_mb=_innodb_log_file_size(total_memory_mb),
        memory_high_mb=memory_high_mb,
        memory_max_mb=memory_max_mb,
    )


def _innodb_log_file_size(total_memory_mb: int) -> int:
    ram_gb = round(total_memory_mb / 1024)
    if ram_gb > 16:
        return 2048
    if ram_gb > 8:
        return 1024
    if ram_gb > 4:
        return 512
    if ram_gb > 2:
        return 128
    return 48
