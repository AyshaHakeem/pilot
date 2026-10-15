from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.bench.runtime import BenchRuntime
from pilot.exceptions import DatabaseError
from pilot.managers.database import MariaDBManager
from tests.pilot.commands.test_commands import make_bench


def _runtime(tmp_path: Path, production: bool = True, existing: bool = False) -> BenchRuntime:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = production
    bench.config.mariadb.existing = existing
    return BenchRuntime(bench)


@pytest.mark.parametrize("reachable", [True, False])
def test_a_production_bench_waits_for_its_own_database(tmp_path: Path, reachable: bool) -> None:
    wait = patch.object(
        MariaDBManager, "wait_until_reachable", side_effect=None if reachable else DatabaseError("down")
    )
    with wait as wait_until_reachable:
        assert _runtime(tmp_path).wait_for_database(120) is reachable

    wait_until_reachable.assert_called_once_with(120)


@pytest.mark.parametrize("production, existing", [(False, False), (True, True)])
def test_no_wait_for_a_development_bench_or_an_existing_server(
    tmp_path: Path, production: bool, existing: bool
) -> None:
    with patch.object(MariaDBManager, "wait_until_reachable") as wait_until_reachable:
        assert _runtime(tmp_path, production, existing).wait_for_database(120)

    wait_until_reachable.assert_not_called()
