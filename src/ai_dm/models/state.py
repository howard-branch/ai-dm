from dataclasses import dataclass, field


@dataclass
class WorldState:
    flags: dict = field(default_factory=dict)
