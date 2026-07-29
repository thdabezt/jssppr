from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

from .model import Instance, Operation


def _integers(text: str, label: str) -> List[int]:
    try:
        return [int(token) for token in text.split()]
    except ValueError as error:
        raise ValueError(f"{label} contains a non-integer value") from error


def _energy_block(
    text: str,
    n_jobs: int,
    n_machines: int,
    label: str,
) -> List[Dict[int, int]]:
    tokens = _integers(text, label)
    if len(tokens) % 2:
        raise ValueError(f"{label} must contain machine/value pairs")

    values: List[Dict[int, int]] = []
    expected_machines = set(range(n_machines))
    machine_values: Dict[int, int] = {}
    for offset in range(0, len(tokens), 2):
        machine = tokens[offset]
        value = tokens[offset + 1]
        if machine not in expected_machines or machine in machine_values:
            continue
        if value < 0:
            job = len(values)
            raise ValueError(f"{label}, job {job}, machine {machine}, has a negative value")
        machine_values[machine] = value
        if len(machine_values) == n_machines:
            values.append(machine_values)
            machine_values = {}

    if len(values) != n_jobs or machine_values:
        raise ValueError(f"{label} must define all {n_machines} machines for {n_jobs} jobs")
    return values


def _threshold_changes(text: str) -> Tuple[Tuple[int, int], ...]:
    tokens = _integers(text, "power-threshold block")
    if not tokens:
        raise ValueError("power-threshold block is empty")

    count = tokens[0]
    if count <= 0 or len(tokens) != 1 + 2 * count:
        raise ValueError("power-threshold block has an invalid change count")

    changes = [(tokens[index], tokens[index + 1]) for index in range(1, len(tokens), 2)]
    changes.sort()
    if changes[0][0] != 0:
        raise ValueError("the first power threshold must start at time 0")
    if len({time for time, _ in changes}) != len(changes):
        raise ValueError("power-threshold change times must be unique")
    if any(time < 0 or power < 0 for time, power in changes):
        raise ValueError("power-threshold values must be non-negative")
    return tuple(changes)


def _thresholds(
    changes: Tuple[Tuple[int, int], ...],
    horizon: int,
) -> Tuple[int, ...]:
    thresholds: List[int] = []
    change_index = 0
    current_power = changes[0][1]
    for time in range(horizon + 1):
        while change_index + 1 < len(changes) and changes[change_index + 1][0] <= time:
            change_index += 1
            current_power = changes[change_index][1]
        thresholds.append(current_power)
    return tuple(thresholds)


def read_instance(path: str | Path) -> Instance:
    source = Path(path).resolve()
    text = source.read_text(encoding="utf-8")
    parts = re.split(r"//\s*part\s*\d+[^\r\n]*", text, flags=re.IGNORECASE)
    if len(parts) != 5:
        raise ValueError(f"{source.name} must contain parts 1 through 4")

    base = _integers(parts[0], "base block")
    if len(base) < 3:
        raise ValueError("base block must start with jobs, machines, and horizon")

    n_jobs, n_machines, horizon = base[:3]
    if n_jobs <= 0 or n_machines <= 0 or horizon <= 0:
        raise ValueError("jobs, machines, and horizon must be positive")

    expected_base = 3 + 2 * n_jobs * n_machines
    if len(base) != expected_base:
        raise ValueError(f"base block must contain {expected_base} integers, found {len(base)}")

    base_jobs: List[List[Tuple[int, int]]] = []
    offset = 3
    expected_machines = set(range(n_machines))
    for job in range(n_jobs):
        operations: List[Tuple[int, int]] = []
        machines = set()
        for _ in range(n_machines):
            machine = base[offset]
            duration = base[offset + 1]
            offset += 2
            if machine not in expected_machines:
                raise ValueError(f"job {job} has invalid machine {machine}")
            if machine in machines:
                raise ValueError(f"job {job} repeats machine {machine}")
            if duration <= 0:
                raise ValueError(f"job {job}, machine {machine}, has a non-positive duration")
            machines.add(machine)
            operations.append((machine, duration))
        base_jobs.append(operations)

    nominal = _energy_block(parts[1], n_jobs, n_machines, "nominal-power block")
    peak = _energy_block(parts[2], n_jobs, n_machines, "peak-power block")
    peak_duration = _energy_block(parts[3], n_jobs, n_machines, "peak-duration block")

    jobs: List[Tuple[Operation, ...]] = []
    operation_id = 0
    for job_index, base_operations in enumerate(base_jobs):
        job_operations: List[Operation] = []
        for operation_index, (machine, duration) in enumerate(base_operations):
            startup_duration = peak_duration[job_index][machine]
            if startup_duration > duration:
                raise ValueError(
                    f"job {job_index}, operation {operation_index}, has peak duration "
                    f"{startup_duration} greater than duration {duration}"
                )
            job_operations.append(
                Operation(
                    id=operation_id,
                    job=job_index,
                    index=operation_index,
                    machine=machine,
                    duration=duration,
                    nominal_power=nominal[job_index][machine],
                    peak_power=peak[job_index][machine],
                    peak_duration=startup_duration,
                )
            )
            operation_id += 1
        jobs.append(tuple(job_operations))

    changes = _threshold_changes(parts[4])
    total_duration = sum(
        operation.duration
        for job in jobs
        for operation in job
    )
    safe_horizon = changes[-1][0] + total_duration
    threshold_horizon = max(horizon, safe_horizon)

    return Instance(
        name=source.name,
        source=source,
        jobs=tuple(jobs),
        power_thresholds=_thresholds(changes, threshold_horizon),
        horizon=horizon,
        safe_horizon=safe_horizon,
    )
