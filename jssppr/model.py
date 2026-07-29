from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Operation:
    id: int
    job: int
    index: int
    machine: int
    duration: int
    nominal_power: int
    peak_power: int
    peak_duration: int


@dataclass(frozen=True)
class Instance:
    name: str
    source: Path
    jobs: Tuple[Tuple[Operation, ...], ...]
    power_thresholds: Tuple[int, ...]
    horizon: int
    safe_horizon: int

    @property
    def n_jobs(self) -> int:
        return len(self.jobs)

    @property
    def n_machines(self) -> int:
        return len(self.jobs[0]) if self.jobs else 0

    @property
    def operations(self) -> Tuple[Operation, ...]:
        return tuple(operation for job in self.jobs for operation in job)


@dataclass
class Domains:
    earliest: Dict[int, int]
    latest: Dict[int, int]
    stats: Dict[str, int]


@dataclass
class SolveResult:
    instance: Instance
    data: Dict[str, object]
    starts: List[List[int]] | None
