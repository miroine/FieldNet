from dataclasses import dataclass, field, asdict
from typing import Dict, Any

@dataclass
class Node:
    id: str
    kind: str
    name: str
    pressure_bar: float | None = None
    x: float = 0.0
    y: float = 0.0
    params: Dict[str, Any] = field(default_factory=dict)
    def to_dict(self): return asdict(self)

@dataclass
class Edge:
    id: str
    source: str
    target: str
    kind: str = "pipeline"
    length_m: float = 1000.0
    diameter_m: float = 0.15
    roughness_m: float = 4.5e-5
    elevation_change_m: float = 0.0
    params: Dict[str, Any] = field(default_factory=dict)
    def to_dict(self): return asdict(self)
