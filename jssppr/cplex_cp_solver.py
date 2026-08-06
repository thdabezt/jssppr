from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .cplex_cp import CplexCpModel, CplexCpUnavailableError, build_cplex_cp_model
from .cplex_cp2 import build_cplex_cp2_model
from .model import Instance, SolveResult
from .preprocess import prepare_instance, verify


BUILDERS = {
    "cplex_cp1": build_cplex_cp_model,
    "cplex_cp2": build_cplex_cp2_model,
}


def _extract_starts(
    instance: Instance,
    operation_intervals: Dict[int, Any],
    solution: Any,
) -> List[List[int]]:
    starts: List[List[int]] = []
    for job in instance.jobs:
        row: List[int] = []
        for operation in job:
            interval_solution = solution.get_var_solution(
                operation_intervals[operation.id]
            )
            if interval_solution is None:
                raise RuntimeError(
                    "CP Optimizer solution is missing operation {}".format(
                        operation.id
                    )
                )
            row.append(int(interval_solution.get_start()))
        starts.append(row)
    return starts


def _search(built_model: CplexCpModel, started: float):
    options: Dict[str, Any] = {
        "Workers": config.CPLEX_CP_WORKERS,
        "SearchType": config.CPLEX_CP_SEARCH_TYPE,
        "LogVerbosity": config.CPLEX_CP_LOG_VERBOSITY,
    }
    if config.TIME_LIMIT_SECONDS is not None:
        options["TimeLimit"] = max(0.0, float(config.TIME_LIMIT_SECONDS))
    if config.CPLEX_CP_EXECFILE:
        options["execfile"] = str(config.CPLEX_CP_EXECFILE)

    try:
        search = built_model.model.start_search(**options)
    except Exception as error:
        raise CplexCpUnavailableError(
            "unable to start IBM CP Optimizer; confirm that cpoptimizer is "
            "installed, licensed, and on PATH, or set CPLEX_CP_EXECFILE in "
            "jssppr/config.py: {}".format(error)
        ) from error

    incumbent = None
    terminal = None
    solution_count = 0
    time_first_solution = None
    time_latest_solution = None
    iterations: List[Dict[str, Any]] = []
    try:
        for result in search:
            terminal = result
            if result is not None and result.is_solution():
                incumbent = result
                solution_count += 1
                elapsed = time.perf_counter() - started
                if time_first_solution is None:
                    time_first_solution = elapsed
                time_latest_solution = elapsed
                iterations.append(
                    {
                        "solution": solution_count,
                        "objective": result.get_objective_value(),
                        "elapsed": elapsed,
                    }
                )
        last = search.get_last_result()
        if last is not None:
            terminal = last
            if incumbent is None and last.is_solution():
                incumbent = last
                solution_count += 1
                elapsed = time.perf_counter() - started
                time_first_solution = elapsed
                time_latest_solution = elapsed
                iterations.append(
                    {
                        "solution": solution_count,
                        "objective": last.get_objective_value(),
                        "elapsed": elapsed,
                    }
                )
    finally:
        try:
            search.end()
        except Exception:
            pass

    return (
        incumbent,
        terminal,
        solution_count,
        time_first_solution,
        time_latest_solution,
        iterations,
    )


def _terminal_text(result: Any, method: str) -> str:
    if result is None:
        return ""
    try:
        value = getattr(result, method)()
    except Exception:
        return ""
    return "" if value is None else str(value)


def _is_optimal(result: Any) -> bool:
    if result is None:
        return False
    try:
        return bool(result.is_solution_optimal())
    except Exception:
        return "optimal" in _terminal_text(result, "get_solve_status").lower()


def _best_bound(result: Any) -> Optional[float]:
    if result is None:
        return None
    try:
        value = result.get_objective_bound()
    except Exception:
        return None
    return None if value is None else float(value)


def solve_instance(path: str | Path) -> SolveResult:
    backend = str(config.BACKEND).lower()
    preparation = prepare_instance(path, backend.replace("_", "-"))
    instance = preparation.instance
    data = preparation.data

    if not preparation.feasible:
        return SolveResult(instance=instance, data=data, starts=None)

    built_model = BUILDERS[backend](
        instance,
        preparation.domains,
        preparation.warm_start,
    )

    build_time = time.perf_counter() - preparation.started
    data["vars"] = built_model.interval_count
    data["clauses"] = built_model.constraint_count
    data["build_time"] = build_time

    (
        incumbent,
        terminal,
        solution_count,
        time_first_solution,
        time_latest_solution,
        iterations,
    ) = _search(built_model, preparation.started)

    starts = None
    makespan = None
    if incumbent is not None:
        starts = _extract_starts(instance, built_model.operation_intervals, incumbent)
        makespan = verify(instance, starts)
        objective = int(round(float(incumbent.get_objective_value())))
        if objective != makespan:
            raise RuntimeError(
                "CP Optimizer objective {} does not match the verified makespan "
                "{}".format(objective, makespan)
            )

    optimal = _is_optimal(terminal) and starts is not None
    best_bound = _best_bound(terminal)
    solve_status = _terminal_text(terminal, "get_solve_status")

    if starts is not None:
        status = "OK"
    elif "infeasible" in solve_status.lower():
        status = "INFEASIBLE"
    else:
        status = "FAILED"

    data.update(
        {
            "status": status,
            "verified": starts is not None,
            "optimal": optimal,
            "makespan": makespan,
            "solve_time": time.perf_counter() - preparation.started,
            "time_first_sat": (
                time_first_solution if time_first_solution is not None else ""
            ),
            "time_latest_sat": (
                time_latest_solution if time_latest_solution is not None else ""
            ),
            "time_unsat": "",
            "best_bound": best_bound if best_bound is not None else "",
            "gap": (
                0.0
                if optimal
                else (
                    (makespan - best_bound) / makespan
                    if makespan and best_bound is not None and makespan > 0
                    else ""
                )
            ),
            "solutions": solution_count,
            "solve_status": solve_status,
            "stop_cause": _terminal_text(terminal, "get_stop_cause"),
            "iterations": iterations,
        }
    )
    print(
        f"[{instance.name}] {data['status']} makespan={makespan} "
        f"optimal={optimal} solutions={solution_count}"
    )
    return SolveResult(instance=instance, data=data, starts=starts)
