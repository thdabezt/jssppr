from __future__ import annotations

from typing import Dict, List

from .model import Domains, Instance, Operation


def _job_bounds(instance: Instance) -> tuple[Dict[int, int], Dict[int, int]]:
    earliest: Dict[int, int] = {}
    latest: Dict[int, int] = {}

    for job in instance.jobs:
        elapsed = 0
        for operation in job:
            earliest[operation.id] = elapsed
            elapsed += operation.duration

        remaining = sum(operation.duration for operation in job)
        for operation in job:
            latest[operation.id] = instance.horizon - remaining
            remaining -= operation.duration

    return earliest, latest


def _naive_bounds(instance: Instance) -> tuple[Dict[int, int], Dict[int, int]]:
    earliest = {operation.id: 0 for operation in instance.operations}
    latest = {
        operation.id: instance.horizon - operation.duration
        for operation in instance.operations
    }
    return earliest, latest


def _propagate_jobs(
    instance: Instance,
    earliest: Dict[int, int],
    latest: Dict[int, int],
) -> int:
    updates = 0
    for job in instance.jobs:
        for index in range(1, len(job)):
            previous = job[index - 1]
            operation = job[index]
            required = earliest[previous.id] + previous.duration
            if earliest[operation.id] < required:
                earliest[operation.id] = required
                updates += 1

        for index in range(len(job) - 2, -1, -1):
            operation = job[index]
            following = job[index + 1]
            required = latest[following.id] - operation.duration
            if latest[operation.id] > required:
                latest[operation.id] = required
                updates += 1
    return updates


def _machine_groups(instance: Instance) -> Dict[int, List[Operation]]:
    groups = {machine: [] for machine in range(instance.n_machines)}
    for operation in instance.operations:
        groups[operation.machine].append(operation)
    return groups


def _propagate_forced_orders(
    instance: Instance,
    earliest: Dict[int, int],
    latest: Dict[int, int],
) -> int:
    updates = 0
    for operations in _machine_groups(instance).values():
        for left_index in range(len(operations)):
            left = operations[left_index]
            for right in operations[left_index + 1 :]:
                if earliest[left.id] + left.duration > latest[right.id]:
                    required_left = earliest[right.id] + right.duration
                    required_right = latest[left.id] - right.duration
                    if earliest[left.id] < required_left:
                        earliest[left.id] = required_left
                        updates += 1
                    if latest[right.id] > required_right:
                        latest[right.id] = required_right
                        updates += 1

                if earliest[right.id] + right.duration > latest[left.id]:
                    required_right = earliest[left.id] + left.duration
                    required_left = latest[right.id] - left.duration
                    if earliest[right.id] < required_right:
                        earliest[right.id] = required_right
                        updates += 1
                    if latest[left.id] > required_left:
                        latest[left.id] = required_left
                        updates += 1
    return updates


def build_domains(
    instance: Instance,
    enabled: bool,
    forced_order: bool,
    max_passes: int,
) -> Domains:
    if enabled:
        earliest, latest = _job_bounds(instance)
    else:
        earliest, latest = _naive_bounds(instance)

    stats = {
        "passes": 0,
        "job_updates": 0,
        "machine_updates": 0,
        "infeasible_operations": 0,
    }

    if enabled and forced_order:
        for _ in range(max(0, max_passes)):
            stats["passes"] += 1
            job_updates = _propagate_jobs(instance, earliest, latest)
            machine_updates = _propagate_forced_orders(instance, earliest, latest)
            stats["job_updates"] += job_updates
            stats["machine_updates"] += machine_updates
            if any(earliest[operation.id] > latest[operation.id] for operation in instance.operations):
                break
            if job_updates + machine_updates == 0:
                break

    stats["infeasible_operations"] = sum(
        earliest[operation.id] > latest[operation.id]
        for operation in instance.operations
    )
    return Domains(earliest=earliest, latest=latest, stats=stats)
