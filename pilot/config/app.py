from dataclasses import dataclass, field


@dataclass
class AppConfig:
    name: str
    repo: str
    branch: str
    branches: list[str] = field(default_factory=list)
    # Update a marketplace app from its branch tip instead of the registry's releases.
    follow_branch: bool = False
