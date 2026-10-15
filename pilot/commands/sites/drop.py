from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class DropSiteCommand(Command):
    name: ClassVar[str] = "drop-site"
    help: ClassVar[str] = "Drop a site, and remove it from bench.toml, nginx and the backup schedule."

    site_name: Annotated[str, Arg(help="Site name.", metavar="site")]
    no_backup: Annotated[bool, Arg(help="Drop without the backup Frappe takes first.")] = False

    def run(self) -> None:
        self.bench.site(self.site_name).drop(on_progress=self.report, no_backup=self.no_backup)
