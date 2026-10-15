from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.commands.runtime.frappe import FrappeCommand
from pilot.commands.sites.drop import DropSiteCommand
from pilot.exceptions import BenchError
from pilot.internal.cli.dispatch import forwarded_frappe_args
from tests.pilot.commands.test_commands import make_bench


@pytest.mark.parametrize("no_backup", [False, True])
def test_drop_site_runs_pilots_drop(tmp_path: Path, no_backup: bool) -> None:
    bench = make_bench(tmp_path)

    with patch("pilot.core.site.Site.drop") as drop:
        DropSiteCommand(bench, site_name="site1.localhost", no_backup=no_backup).run()

    assert drop.call_args.kwargs["no_backup"] is no_backup


def test_drop_site_is_a_pilot_command() -> None:
    assert forwarded_frappe_args(["drop-site", "site1.localhost"]) is None


@pytest.mark.parametrize(
    "args", [("--site", "site1.localhost", "drop-site", "site1.localhost"), ("drop-site", "site1.localhost")]
)
def test_frappes_drop_site_is_refused(tmp_path: Path, args: tuple[str, ...]) -> None:
    bench = make_bench(tmp_path)

    with patch("subprocess.run") as run, pytest.raises(BenchError, match="pilot drop-site SITE"):
        FrappeCommand(bench, args=args).run()

    run.assert_not_called()
