from __future__ import annotations

import json
import typing

from pilot.core.app.validator.base import module_path
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App


class FixturesCheck:
    """Parse every fixture file, since frappe imports them during migrate."""

    def run(self, app: "App") -> None:
        broken = []
        for path in sorted((module_path(app) / "fixtures").glob("*.json")):
            try:
                json.loads(path.read_text())
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                broken.append(f"{path.relative_to(app.path)}: {exc}")

        if broken:
            raise AppValidationError(
                f"'{app.config.name}' has fixtures that aren't valid JSON:\n"
                + "\n".join(f"  {problem}" for problem in broken)
                + "\nFix the JSON or drop the file - frappe imports these during migrate."
            )
