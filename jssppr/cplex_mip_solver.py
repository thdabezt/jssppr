from __future__ import annotations

import time
from math import isfinite
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .cplex_mip import (
    CplexMipModel,
    build_cplex_mip_model,
    extract_start_matrix,
    load_docplex_symbols,
    safe_end,
)
from .model import SolveResult
from .preprocess import prepare_instance, verify


def _configure(model: Any) -> None:
    if config.TIME_LIMIT_SECONDS is not None:
        model.parameters.timelimit.set(max(0.0, float(config.TIME_LIMIT_SECONDS)))
    model.parameters.threads.set(int(config.CPLEX_MIP_THREADS))
    model.parameters.mip.tolerances.mipgap.set(float(config.CPLEX_MIP_RELATIVE_GAP))
    model.parameters.mip.tolerances.absmipgap.set(
        float(config.CPLEX_MIP_ABSOLUTE_GAP)
    )
    if config.CPLEX_MIP_RANDOM_SEED is not None:
        model.parameters.randomseed.set(int(config.CPLEX_MIP_RANDOM_SEED))


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _detail_value(details: Any, *names: str) -> Any:
    if details is None:
        return None
    for name in names:
        try:
            value = getattr(details, name)
        except (AttributeError, TypeError):
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if value is not None:
            return value
    return None


def _search(built_model: CplexMipModel, started: float):
    symbols = load_docplex_symbols()
    model = built_model.model
    iterations: List[Dict[str, Any]] = []
    timings: Dict[str, Optional[float]] = {"first": None, "latest": None}

    class _IncumbentListener(symbols["SolutionListener"]):
        def __init__(self) -> None:
            super().__init__(clock=symbols["ProgressClock"].Solutions)
            self.solution_count = 0

        def notify_solution(self, solution: Any) -> None:
            self.solution_count += 1
            elapsed = time.perf_counter() - started
            if timings["first"] is None:
                timings["first"] = elapsed
            timings["latest"] = elapsed
            iterations.append(
                {
                    "solution": self.solution_count,
                    "objective": solution.objective_value,
                    "elapsed": elapsed,
                }
            )

    listener = _IncumbentListener()
    model.add_progress_listener(listener)

    incumbent = model.solve(log_output=bool(config.CPLEX_MIP_LOG_OUTPUT))
    details = model.solve_details
    if incumbent is None:
        incumbent = model.solution
    if incumbent is not None and listener.solution_count == 0:
        listener.solution_count = 1
        elapsed = time.perf_counter() - started
        timings["first"] = elapsed
        timings["latest"] = elapsed
        iterations.append(
            {
                "solution": listener.solution_count,
                "objective": incumbent.objective_value,
                "elapsed": elapsed,
            }
        )

    return incumbent, details, listener.solution_count, timings, iterations


def solve_instance(path: str | Path) -> SolveResult:
    preparation = prepare_instance(path, "cplex-mip")
    instance = preparation.instance
    data = preparation.data

    if not preparation.feasible:
        return SolveResult(instance=instance, data=data, starts=None)

    built_model = build_cplex_mip_model(
        instance,
        preparation.domains,
        preparation.warm_start,
    )

    data["vars"] = built_model.variable_count
    data["clauses"] = built_model.constraint_count
    data["build_time"] = time.perf_counter() - preparation.started

    starts = None
    makespan = None
    try:
        _configure(built_model.model)
        incumbent, details, solution_count, timings, iterations = _search(
            built_model, preparation.started
        )

        if incumbent is not None:
            starts = extract_start_matrix(
                instance,
                built_model.start_variables,
                incumbent,
            )
            makespan = verify(instance, starts)

        status = str(_detail_value(details, "status", "status_string") or "")
        lowered = status.lower()
        optimal = (
            starts is not None
            and "optimal" in lowered
            and "not optimal" not in lowered
        )
        best_bound = _finite_float(_detail_value(details, "best_bound"))
        gap = _finite_float(
            _detail_value(details, "mip_relative_gap", "mip_gap")
        )
        nodes = _detail_value(details, "nb_nodes_processed", "node_count")
    finally:
        safe_end(built_model.model)

    if starts is not None:
        final_status = "OK"
    elif "infeasible" in status.lower():
        final_status = "INFEASIBLE"
    else:
        final_status = "FAILED"

    data.update(
        {
            "status": final_status,
            "verified": starts is not None,
            "optimal": optimal,
            "makespan": makespan,
            "solve_time": time.perf_counter() - preparation.started,
            "time_first_sat": timings["first"] if timings["first"] is not None else "",
            "time_latest_sat": (
                timings["latest"] if timings["latest"] is not None else ""
            ),
            "time_unsat": "",
            "best_bound": best_bound if best_bound is not None else "",
            "gap": 0.0 if optimal else (gap if gap is not None else ""),
            "solutions": solution_count,
            "solve_status": status,
            "nodes": int(nodes) if _finite_float(nodes) is not None else "",
            "iterations": iterations,
        }
    )
    print(
        f"[{instance.name}] {data['status']} makespan={makespan} "
        f"optimal={optimal} solutions={solution_count}"
    )
    return SolveResult(instance=instance, data=data, starts=starts)
