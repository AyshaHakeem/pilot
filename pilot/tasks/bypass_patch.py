import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.exceptions import BenchError
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class BypassPatchTask(Task):
    command: ClassVar[str] = "bypass-patch"

    operation_id: str
    patch: str

    def run(self) -> None:
        self.bypass()

    @step("bypass_patch", lambda self: f"Skip patch {self.patch}")
    def bypass(self) -> None:
        operation = self.bench.migrations.get(self.operation_id)
        try:
            operation.bypass_patch(self.patch, on_progress=self.report)
        except BenchError as error:
            self.report(str(error))
            sys.exit(1)
        operation.enqueue_next(handoff_from=operation.task_ids.get("bypass_patch"))


if __name__ == "__main__":
    BypassPatchTask.main()
