from dataclasses import dataclass, field


@dataclass
class AppConfig:
    name: str
    repo: str
    branch: str
    branches: list[str] = field(default_factory=list)
    # True: updates use the branch tip, not the registry's releases.
    follow_branch: bool = False
