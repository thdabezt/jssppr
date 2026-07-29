from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List

from pysat.solvers import Solver

from . import config, encoding0, encoding1, encoding1_extra
from .encoding_common import (
    POWER_BOUND_ENCODING,
    ClauseCounter,
    Variables,
    add_base_constraints,
    add_makespan_bound,
)
from .heuristic_ub import find_upper_bound
from .model import Instance, SolveResult
from .parser import read_instance
from .preprocess import build_domains


ENCODINGS = {
    "encoding0": encoding0.apply,
    "encoding1": encoding1.apply,
    "encoding1_extra": encoding1_extra.apply,
}


def _decode_starts(
    instance: Instance,
    variables: Variables,
    model: List[int],
) -> List[List[int]]:
    positive = {literal for literal in model if literal > 0}
    starts: List[List[int]] = []
    for job in instance.jobs:
        row = []
        for operation in job:
            earliest = variables.domains.earliest[operation.id]
            latest = variables.domains.latest[operation.id]
            selected = [
                time
                for time in range(earliest, latest + 1)
                if variables.start(operation.id, time) in positive
            ]
            if len(selected) != 1:
                raise RuntimeError(
                    f"model selected {len(selected)} starts for operation {operation.id}"
                )
            row.append(selected[0])
        starts.append(row)
    return starts


def _verify(instance: Instance, starts: List[List[int]]) -> int:
    intervals: Dict[int, List[tuple[int, int, int]]] = {
        machine: [] for machine in range(instance.n_machines)
    }
    power = [0] * instance.horizon
    makespan = 0

    for job_index, job in enumerate(instance.jobs):
        if len(starts[job_index]) != len(job):
            raise RuntimeError(f"job {job_index} has an incomplete schedule")
        previous_end = 0
        for operation_index, operation in enumerate(job):
            start = starts[job_index][operation_index]
            end = start + operation.duration
            if start < previous_end:
                raise RuntimeError(
                    f"job {job_index}, operation {operation_index}, violates precedence"
                )
            if start < 0 or end > instance.horizon:
                raise RuntimeError(
                    f"job {job_index}, operation {operation_index}, exceeds the horizon"
                )
            intervals[operation.machine].append((start, end, operation.id))
            for time in range(start, end):
                power[time] += operation.nominal_power
                if time - start < operation.peak_duration:
                    power[time] += operation.peak_power
            previous_end = end
            makespan = max(makespan, end)

    for machine, machine_intervals in intervals.items():
        machine_intervals.sort()
        for left, right in zip(machine_intervals, machine_intervals[1:]):
            if left[1] > right[0]:
                raise RuntimeError(
                    f"machine {machine} overlaps operations {left[2]} and {right[2]}"
                )

    for time, used in enumerate(power):
        if used > instance.power_thresholds[time]:
            raise RuntimeError(
                f"power use {used} exceeds {instance.power_thresholds[time]} at time {time}"
            )
    return makespan


def _with_horizon(instance: Instance, horizon: int) -> Instance:
    if horizon == instance.horizon:
        return instance
    return Instance(
        name=instance.name,
        source=instance.source,
        jobs=instance.jobs,
        power_thresholds=instance.power_thresholds[: horizon + 1],
        horizon=horizon,
        safe_horizon=instance.safe_horizon,
    )


def solve_instance(path: str | Path) -> SolveResult:
    total_started = time.perf_counter()
    dataset_instance = read_instance(path)

    encoding_name = str(config.ENCODING).lower()
    if encoding_name not in ENCODINGS:
        raise ValueError(f"unsupported encoding: {config.ENCODING}")

    heuristic_started = time.perf_counter()
    heuristic_ub = None
    heuristic_starts = None
    heuristic_iterations = 0
    heuristic_instance = dataset_instance
    if (
        bool(config.HEURISTIC_UB)
        and not bool(config.HEURISTIC_USE_DATASET_UB)
    ):
        heuristic_instance = _with_horizon(
            dataset_instance,
            dataset_instance.safe_horizon,
        )
    if bool(config.HEURISTIC_UB):
        heuristic_ub, heuristic_starts, heuristic_iterations = find_upper_bound(
            heuristic_instance,
            time_limit=float(config.HEURISTIC_TIME_LIMIT),
            seed=int(config.HEURISTIC_SEED),
        )
    heuristic_time = time.perf_counter() - heuristic_started
    ub_source = "dataset"
    instance = dataset_instance

    if heuristic_ub is not None and heuristic_starts is not None:
        verified_ub = _verify(heuristic_instance, heuristic_starts)
        if verified_ub != heuristic_ub:
            raise RuntimeError(
                f"heuristic makespan {heuristic_ub} verified as {verified_ub}"
            )
        instance = _with_horizon(dataset_instance, heuristic_ub)
        ub_source = "heuristic"
        print(
            f"[{instance.name}] heuristic UB={heuristic_ub} "
            f"search_horizon={heuristic_instance.horizon} "
            f"iterations={heuristic_iterations} "
            f"time={heuristic_time:.6f}s"
        )
    elif bool(config.HEURISTIC_UB):
        instance = heuristic_instance
        ub_source = (
            "dataset"
            if bool(config.HEURISTIC_USE_DATASET_UB)
            else "safe_horizon"
        )
        print(
            f"[{instance.name}] heuristic found no schedule; "
            f"using {ub_source} UB={instance.horizon}"
        )

    domains = build_domains(
        instance,
        enabled=bool(config.PREPROCESS),
        forced_order=bool(config.PREPROCESS_FORCED_ORDER),
        max_passes=int(config.PREPROCESS_MAX_PASSES),
    )

    base_data: Dict[str, object] = {
        "instance": instance.name,
        "source": str(instance.source),
        "solver": f"{config.SOLVER}-pb",
        "encoding": encoding_name,
        "pb_encoding_used": POWER_BOUND_ENCODING,
        "jobs": instance.n_jobs,
        "machines": instance.n_machines,
        "operations": len(instance.operations),
        "horizon": instance.horizon,
        "dataset_horizon": dataset_instance.horizon,
        "safe_horizon": dataset_instance.safe_horizon,
        "UB": instance.horizon,
        "ub_source": ub_source,
        "heuristic_ub": heuristic_ub if heuristic_ub is not None else "",
        "heuristic_time": heuristic_time,
        "heuristic_iterations": heuristic_iterations,
        "preprocessing": domains.stats,
    }

    if domains.stats["infeasible_operations"]:
        base_data.update(
            {
                "status": "FAILED",
                "verified": False,
                "makespan": None,
                "vars": 0,
                "clauses": 0,
                "build_time": 0.0,
                "solve_time": time.perf_counter() - total_started,
                "time_first_sat": "",
                "time_latest_sat": "",
                "time_unsat": "",
                "iterations": [],
            }
        )
        return SolveResult(instance=instance, data=base_data, starts=None)

    variables = Variables(domains, instance.horizon)
    solver = Solver(name=str(config.SOLVER))
    clauses = ClauseCounter(solver)

    try:
        build_started = time.perf_counter()
        add_base_constraints(instance, variables, clauses)
        ENCODINGS[encoding_name](instance, variables, clauses)
        build_time = time.perf_counter() - build_started

        current_bound = instance.horizon
        best_starts = None
        best_makespan = None
        time_first_sat = None
        time_latest_sat = None
        time_unsat = None
        iterations = []

        while current_bound >= 0:
            add_makespan_bound(instance, variables, clauses, current_bound)
            iteration_started = time.perf_counter()
            satisfiable = solver.solve()
            iteration_time = time.perf_counter() - iteration_started
            elapsed = time.perf_counter() - total_started

            if not satisfiable:
                time_unsat = iteration_time
                iterations.append(
                    {
                        "bound": current_bound,
                        "status": "UNSAT",
                        "iteration_time": iteration_time,
                        "elapsed": elapsed,
                    }
                )
                print(
                    f"[{instance.name}] UNSAT Cmax<={current_bound} "
                    f"proof_time={iteration_time:.6f}s"
                )
                break

            candidate_starts = _decode_starts(
                instance,
                variables,
                solver.get_model(),
            )
            candidate_makespan = _verify(instance, candidate_starts)
            if candidate_makespan > current_bound:
                raise RuntimeError(
                    f"model makespan {candidate_makespan} exceeds bound {current_bound}"
                )

            best_starts = candidate_starts
            best_makespan = candidate_makespan
            if time_first_sat is None:
                time_first_sat = elapsed
            time_latest_sat = elapsed
            iterations.append(
                {
                    "bound": current_bound,
                    "status": "SAT",
                    "makespan": candidate_makespan,
                    "iteration_time": iteration_time,
                    "elapsed": elapsed,
                }
            )
            print(
                f"[{instance.name}] SAT Cmax<={current_bound} "
                f"makespan={candidate_makespan} "
                f"iteration_time={iteration_time:.6f}s"
            )
            current_bound = candidate_makespan - 1

        verified = best_starts is not None
        status = "OK" if verified else "FAILED"

        base_data.update(
            {
                "status": status,
                "verified": verified,
                "makespan": best_makespan,
                "vars": variables.pool.top,
                "clauses": clauses.count,
                "build_time": build_time,
                "solve_time": time.perf_counter() - total_started,
                "time_first_sat": (
                    time_first_sat if time_first_sat is not None else ""
                ),
                "time_latest_sat": (
                    time_latest_sat if time_latest_sat is not None else ""
                ),
                "time_unsat": time_unsat if time_unsat is not None else "",
                "iterations": iterations,
            }
        )
        return SolveResult(
            instance=instance,
            data=base_data,
            starts=best_starts,
        )
    finally:
        solver.delete()
