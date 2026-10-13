from dataclasses import dataclass, field


@dataclass
class AppConfig:
    name: str
    repo: str
    branch: str
    commit: str = ""
    branches: list[str] = field(default_factory=list)
