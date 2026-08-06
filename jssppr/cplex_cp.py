from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .model import Domains, Instance, Operation


class CplexCpUnavailableError(RuntimeError):
    pass


@dataclass
class CplexCpModel:
    model: Any
    operation_intervals: Dict[int, Any]
    makespan: Any
    horizon: int
    interval_count: int
    constraint_count: int


def compress_power_thresholds(
    power_thresholds: Sequence[int],
    horizon: int,
) -> List[Tuple[int, int, int]]:
    if horizon < 0:
        raise ValueError("horizon must be non-negative")
    if horizon == 0:
        return []
    if not power_thresholds:
        raise ValueError("power_thresholds cannot be empty")

    def capacity_at(time_index: int) -> int:
        index = min(time_index, len(power_thresholds) - 1)
        return int(power_thresholds[index])

    segments: List[Tuple[int, int, int]] = []
    segment_start = 0
    capacity = capacity_at(0)
    for time_index in range(1, horizon):
        next_capacity = capacity_at(time_index)
        if next_capacity != capacity:
            segments.append((segment_start, time_index, capacity))
            segment_start = time_index
            capacity = next_capacity
    segments.append((segment_start, horizon, capacity))
    return segments


def effective_peak_duration(operation: Operation) -> int:
    duration = max(0, int(operation.duration))
    return min(duration, max(0, int(operation.peak_duration)))


def load_docplex_symbols() -> Dict[str, Any]:
    try:
        from docplex.cp.expression import interval_var
        from docplex.cp.model import CpoModel
        from docplex.cp.modeler import (
            always_in,
            end_before_start,
            end_of,
            max_of,
            minimize,
            no_overlap,
            pulse,
            start_at_start,
            start_of,
        )
    except ImportError as error:
        raise CplexCpUnavailableError(
            "the CP Optimizer backends require the 'docplex' package and a "
            "licensed IBM CP Optimizer runtime; install IBM CPLEX Optimization "
            "Studio and make its cpoptimizer executable discoverable, or set "
            "CPLEX_CP_EXECFILE in jssppr/config.py"
        ) from error

    return {
        "CpoModel": CpoModel,
        "interval_var": interval_var,
        "always_in": always_in,
        "end_before_start": end_before_start,
        "end_of": end_of,
        "max_of": max_of,
        "minimize": minimize,
        "no_overlap": no_overlap,
        "pulse": pulse,
        "start_at_start": start_at_start,
        "start_of": start_of,
    }


def build_cplex_cp_model(
    instance: Instance,
    domains: Domains,
    warm_start: Optional[Sequence[Sequence[int]]] = None,
    model_name: str = "JSSPPR_CPLEX_CP1",
) -> CplexCpModel:
    if not instance.jobs or not instance.jobs[0]:
        raise ValueError("instance has no operations")
    if instance.horizon <= 0:
        raise ValueError("horizon must be positive")

    symbols = load_docplex_symbols()
    model = symbols["CpoModel"](name=model_name)
    operation_intervals: Dict[int, Any] = {}
    startup_intervals: Dict[int, Any] = {}
    constraints = 0

    for job in instance.jobs:
        for operation in job:
            operation_intervals[operation.id] = symbols["interval_var"](
                start=(
                    domains.earliest[operation.id],
                    domains.latest[operation.id],
                ),
                size=operation.duration,
                name="op_j{}_o{}_id{}".format(
                    operation.job, operation.index, operation.id
                ),
            )

    for job in instance.jobs:
        for predecessor, successor in zip(job, job[1:]):
            model.add(
                symbols["end_before_start"](
                    operation_intervals[predecessor.id],
                    operation_intervals[successor.id],
                )
            )
            constraints += 1

    by_machine: Dict[int, List[Any]] = {}
    for operation in instance.operations:
        by_machine.setdefault(operation.machine, []).append(
            operation_intervals[operation.id]
        )
    for machine_intervals in by_machine.values():
        if len(machine_intervals) > 1:
            model.add(symbols["no_overlap"](machine_intervals))
            constraints += 1

    power_terms: List[Any] = []
    for operation in instance.operations:
        operation_interval = operation_intervals[operation.id]
        peak_duration = effective_peak_duration(operation)

        if operation.nominal_power > 0:
            power_terms.append(
                symbols["pulse"](operation_interval, operation.nominal_power)
            )

        if operation.peak_power > 0 and peak_duration > 0:
            startup = symbols["interval_var"](
                size=peak_duration,
                name="startup_id{}".format(operation.id),
            )
            startup_intervals[operation.id] = startup
            model.add(symbols["start_at_start"](operation_interval, startup))
            constraints += 1
            power_terms.append(symbols["pulse"](startup, operation.peak_power))

    if power_terms:
        power_usage = power_terms[0]
        for term in power_terms[1:]:
            power_usage = power_usage + term
        for segment_start, segment_end, capacity in compress_power_thresholds(
            instance.power_thresholds, instance.horizon
        ):
            model.add(
                symbols["always_in"](
                    power_usage,
                    (segment_start, segment_end),
                    0,
                    capacity,
                )
            )
            constraints += 1

    makespan = symbols["max_of"](
        [
            symbols["end_of"](operation_intervals[job[-1].id])
            for job in instance.jobs
        ]
    )
    model.add(symbols["minimize"](makespan))

    if warm_start is not None:
        starting_point = model.create_empty_solution()
        for job_index, job in enumerate(instance.jobs):
            for operation_index, operation in enumerate(job):
                start = int(warm_start[job_index][operation_index])
                starting_point.add_interval_var_solution(
                    operation_intervals[operation.id],
                    presence=True,
                    start=start,
                    end=start + operation.duration,
                    size=operation.duration,
                )
                startup = startup_intervals.get(operation.id)
                if startup is not None:
                    peak_duration = effective_peak_duration(operation)
                    starting_point.add_interval_var_solution(
                        startup,
                        presence=True,
                        start=start,
                        end=start + peak_duration,
                        size=peak_duration,
                    )
        model.set_starting_point(starting_point)

    return CplexCpModel(
        model=model,
        operation_intervals=operation_intervals,
        makespan=makespan,
        horizon=instance.horizon,
        interval_count=len(operation_intervals) + len(startup_intervals),
        constraint_count=constraints,
    )
