from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Sequence

from . import config
from .heuristic_ub import find_upper_bound
from .model import Domains, Instance, Preparation
from .parser import read_instance


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


def build_domains(instance: Instance, enabled: bool) -> Domains:
    if enabled:
        earliest, latest = _job_bounds(instance)
    else:
        earliest, latest = _naive_bounds(instance)

    stats = {
        "infeasible_operations": sum(
            earliest[operation.id] > latest[operation.id]
            for operation in instance.operations
        )
    }
    return Domains(earliest=earliest, latest=latest, stats=stats)


def with_horizon(instance: Instance, horizon: int) -> Instance:
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


def verify(instance: Instance, starts: Sequence[Sequence[int]]) -> int:
    if len(starts) != instance.n_jobs:
        raise RuntimeError("schedule has the wrong number of jobs")

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
            start = int(starts[job_index][operation_index])
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
            for time_index in range(start, end):
                power[time_index] += operation.nominal_power
                if time_index - start < operation.peak_duration:
                    power[time_index] += operation.peak_power
            previous_end = end
            makespan = max(makespan, end)

    for machine, machine_intervals in intervals.items():
        machine_intervals.sort()
        for left, right in zip(machine_intervals, machine_intervals[1:]):
            if left[1] > right[0]:
                raise RuntimeError(
                    f"machine {machine} overlaps operations {left[2]} and {right[2]}"
                )

    for time_index, used in enumerate(power):
        if used > instance.power_thresholds[time_index]:
            raise RuntimeError(
                f"power use {used} exceeds "
                f"{instance.power_thresholds[time_index]} at time {time_index}"
            )
    return makespan


def load_ub_cache(path: str | Path) -> Dict[str, int]:
    cache: Dict[str, int] = {}
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"upper-bound cache not found: {source}")
    for number, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"{source.name} line {number}: expected name:bound")
        name, value = line.rsplit(":", 1)
        try:
            bound = int(value)
        except ValueError as error:
            raise ValueError(
                f"{source.name} line {number}: {value.strip()!r} is not an integer"
            ) from error
        if bound <= 0:
            raise ValueError(f"{source.name} line {number}: bound must be positive")
        cache[name.strip()] = bound
    return cache


def _heuristic_warm_start(dataset_instance: Instance, bound: int):
    search_instance = with_horizon(
        dataset_instance,
        dataset_instance.safe_horizon,
    )
    makespan, starts, iterations = find_upper_bound(
        search_instance,
        time_limit=float(config.HEURISTIC_TIME_LIMIT),
        seed=int(config.HEURISTIC_SEED),
    )
    if makespan is None or starts is None:
        return None, iterations
    if verify(search_instance, starts) != makespan:
        raise RuntimeError(
            f"heuristic makespan {makespan} failed verification"
        )
    return (starts if makespan <= bound else None), iterations


def prepare_instance(path: str | Path, solver: str) -> Preparation:
    started = time.perf_counter()
    dataset_instance = read_instance(path)

    horizon_mode = str(config.HORIZON).lower()
    if horizon_mode not in ("heuristic", "cache", "safe", "dataset"):
        raise ValueError(
            f"unsupported horizon {config.HORIZON!r}; "
            f"choose one of: heuristic, cache, safe, dataset"
        )

    heuristic_ub = None
    heuristic_iterations = 0
    heuristic_time = 0.0
    warm_start = None

    if horizon_mode == "dataset":
        instance = dataset_instance
        ub_source = "dataset"
        print(f"[{instance.name}] dataset horizon H={instance.horizon}")
    elif horizon_mode == "cache":
        cache = load_ub_cache(config.UB_CACHE_FILE)
        if dataset_instance.name not in cache:
            raise KeyError(
                f"{dataset_instance.name} has no entry in "
                f"{Path(config.UB_CACHE_FILE).name}"
            )
        heuristic_ub = cache[dataset_instance.name]
        instance = with_horizon(dataset_instance, heuristic_ub)
        ub_source = "cache"
        if str(config.BACKEND).lower() == "sat":
            print(f"[{instance.name}] cached UB={heuristic_ub}")
        else:
            heuristic_started = time.perf_counter()
            warm_start, heuristic_iterations = _heuristic_warm_start(
                dataset_instance,
                heuristic_ub,
            )
            heuristic_time = time.perf_counter() - heuristic_started
            print(
                f"[{instance.name}] cached UB={heuristic_ub} "
                f"warm_start={'yes' if warm_start is not None else 'no'} "
                f"time={heuristic_time:.6f}s"
            )
    elif horizon_mode == "safe":
        instance = with_horizon(dataset_instance, dataset_instance.safe_horizon)
        ub_source = "safe_horizon"
        print(f"[{instance.name}] no upper bound; safe horizon H={instance.horizon}")
    else:
        heuristic_started = time.perf_counter()
        search_instance = with_horizon(
            dataset_instance,
            dataset_instance.safe_horizon,
        )
        heuristic_ub, heuristic_starts, heuristic_iterations = find_upper_bound(
            search_instance,
            time_limit=float(config.HEURISTIC_TIME_LIMIT),
            seed=int(config.HEURISTIC_SEED),
        )
        heuristic_time = time.perf_counter() - heuristic_started

        if heuristic_ub is not None and heuristic_starts is not None:
            verified_ub = verify(search_instance, heuristic_starts)
            if verified_ub != heuristic_ub:
                raise RuntimeError(
                    f"heuristic makespan {heuristic_ub} verified as {verified_ub}"
                )
            instance = with_horizon(dataset_instance, heuristic_ub)
            warm_start = heuristic_starts
            ub_source = "heuristic"
            print(
                f"[{instance.name}] heuristic UB={heuristic_ub} "
                f"search_horizon={search_instance.horizon} "
                f"iterations={heuristic_iterations} "
                f"time={heuristic_time:.6f}s"
            )
        else:
            instance = search_instance
            ub_source = "safe_horizon"
            print(
                f"[{instance.name}] heuristic found no schedule; "
                f"using safe horizon H={instance.horizon}"
            )

    domains = build_domains(instance, enabled=bool(config.PREPROCESS))

    data: Dict[str, object] = {
        "instance": instance.name,
        "source": str(instance.source),
        "backend": str(config.BACKEND).lower(),
        "solver": solver,
        "encoding": "",
        "pb_encoding_used": "",
        "jobs": instance.n_jobs,
        "machines": instance.n_machines,
        "operations": len(instance.operations),
        "horizon": instance.horizon,
        "dataset_horizon": dataset_instance.horizon,
        "safe_horizon": dataset_instance.safe_horizon,
        "UB": instance.horizon,
        "horizon_mode": horizon_mode,
        "ub_source": ub_source,
        "heuristic_ub": heuristic_ub if heuristic_ub is not None else "",
        "heuristic_time": heuristic_time,
        "heuristic_iterations": heuristic_iterations,
        "preprocessing": domains.stats,
    }

    feasible = not domains.stats["infeasible_operations"]
    if not feasible:
        data.update(
            {
                "status": "FAILED",
                "verified": False,
                "optimal": False,
                "makespan": None,
                "vars": 0,
                "clauses": 0,
                "build_time": 0.0,
                "solve_time": time.perf_counter() - started,
                "time_first_sat": "",
                "time_latest_sat": "",
                "time_unsat": "",
                "best_bound": "",
                "gap": "",
                "iterations": [],
            }
        )

    return Preparation(
        dataset_instance=dataset_instance,
        instance=instance,
        domains=domains,
        warm_start=warm_start,
        data=data,
        started=started,
        feasible=feasible,
    )
