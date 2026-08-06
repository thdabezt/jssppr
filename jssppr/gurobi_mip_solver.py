from __future__ import annotations

import time
from math import isfinite
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config
from .gurobi_mip import (
    GurobiMipModel,
    build_gurobi_mip_model,
    extract_start_matrix,
    load_gurobi_symbols,
    translate_license_error,
    safe_dispose,
)
from .model import SolveResult
from .preprocess import prepare_instance, verify


def _configure(model: Any) -> None:
    if config.TIME_LIMIT_SECONDS is not None:
        model.Params.TimeLimit = max(0.0, float(config.TIME_LIMIT_SECONDS))
    model.Params.Threads = int(config.GUROBI_THREADS)
    model.Params.MIPGap = float(config.GUROBI_RELATIVE_GAP)
    model.Params.MIPGapAbs = float(config.GUROBI_ABSOLUTE_GAP)
    if config.GUROBI_RANDOM_SEED is not None:
        model.Params.Seed = int(config.GUROBI_RANDOM_SEED)
    model.Params.OutputFlag = 1 if config.GUROBI_LOG_OUTPUT else 0


def _finite_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _attribute(model: Any, name: str) -> Any:
    try:
        return getattr(model, name)
    except Exception:
        return None


def _search(built_model: GurobiMipModel, started: float):
    symbols = load_gurobi_symbols()
    GRB = symbols["GRB"]
    model = built_model.model
    iterations: List[Dict[str, Any]] = []
    timings: Dict[str, Optional[float]] = {"first": None, "latest": None}
    counter = {"solutions": 0}

    def callback(callback_model: Any, where: int) -> None:
        if where != GRB.Callback.MIPSOL:
            return
        counter["solutions"] += 1
        elapsed = time.perf_counter() - started
        if timings["first"] is None:
            timings["first"] = elapsed
        timings["latest"] = elapsed
        iterations.append(
            {
                "solution": counter["solutions"],
                "objective": callback_model.cbGet(GRB.Callback.MIPSOL_OBJ),
                "elapsed": elapsed,
            }
        )

    try:
        model.optimize(callback)
    except Exception as error:
        translated = translate_license_error(error)
        if translated is not error:
            raise translated from error
        raise
    return counter["solutions"], timings, iterations


def solve_instance(path: str | Path) -> SolveResult:
    preparation = prepare_instance(path, "gurobi-mip")
    instance = preparation.instance
    data = preparation.data

    if not preparation.feasible:
        return SolveResult(instance=instance, data=data, starts=None)

    symbols = load_gurobi_symbols()
    GRB = symbols["GRB"]

    built_model = build_gurobi_mip_model(
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
        solution_count, timings, iterations = _search(
            built_model, preparation.started
        )

        model = built_model.model
        status = int(_attribute(model, "Status") or 0)
        available = int(_attribute(model, "SolCount") or 0)

        if available > 0:
            starts = extract_start_matrix(instance, built_model.start_variables)
            makespan = verify(instance, starts)
            objective = _finite_float(_attribute(model, "ObjVal"))
            if objective is not None and int(round(objective)) != makespan:
                raise RuntimeError(
                    "Gurobi objective {} does not match the verified makespan "
                    "{}".format(int(round(objective)), makespan)
                )

        optimal = starts is not None and status == GRB.OPTIMAL
        infeasible = status in (GRB.INFEASIBLE, GRB.INF_OR_UNBD)
        best_bound = _finite_float(_attribute(model, "ObjBound"))
        gap = _finite_float(_attribute(model, "MIPGap"))
        nodes = _finite_float(_attribute(model, "NodeCount"))
        status_name = {
            GRB.OPTIMAL: "OPTIMAL",
            GRB.INFEASIBLE: "INFEASIBLE",
            GRB.INF_OR_UNBD: "INF_OR_UNBD",
            GRB.UNBOUNDED: "UNBOUNDED",
            GRB.TIME_LIMIT: "TIME_LIMIT",
            GRB.INTERRUPTED: "INTERRUPTED",
            GRB.SUBOPTIMAL: "SUBOPTIMAL",
        }.get(status, str(status))
    finally:
        safe_dispose(built_model.model)

    if starts is not None:
        final_status = "OK"
    elif infeasible:
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
            "solve_status": status_name,
            "nodes": int(nodes) if nodes is not None else "",
            "iterations": iterations,
        }
    )
    print(
        f"[{instance.name}] {data['status']} makespan={makespan} "
        f"optimal={optimal} solutions={solution_count}"
    )
    return SolveResult(instance=instance, data=data, starts=starts)
