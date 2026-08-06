from __future__ import annotations

import time
from pathlib import Path
from typing import List

from pysat.solvers import Solver

from . import config, encoding1
from .encoding_common import (
    POWER_BOUND_ENCODING,
    ClauseCounter,
    Variables,
    add_base_constraints,
    add_makespan_bound,
)
from .model import Instance, SolveResult
from .preprocess import prepare_instance, verify


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


def solve_instance(path: str | Path) -> SolveResult:
    preparation = prepare_instance(path, f"{config.SOLVER}-pb")
    instance = preparation.instance
    data = preparation.data
    data["encoding"] = "encoding1"
    data["pb_encoding_used"] = POWER_BOUND_ENCODING

    if not preparation.feasible:
        return SolveResult(instance=instance, data=data, starts=None)

    variables = Variables(preparation.domains, instance.horizon)
    solver = Solver(name=str(config.SOLVER))
    clauses = ClauseCounter(solver)

    try:
        build_started = time.perf_counter()
        add_base_constraints(instance, variables, clauses)
        encoding1.apply(instance, variables, clauses)
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
            elapsed = time.perf_counter() - preparation.started

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
            candidate_makespan = verify(instance, candidate_starts)
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

        data.update(
            {
                "status": status,
                "verified": verified,
                "optimal": verified and time_unsat is not None,
                "makespan": best_makespan,
                "vars": variables.pool.top,
                "clauses": clauses.count,
                "build_time": build_time,
                "solve_time": time.perf_counter() - preparation.started,
                "time_first_sat": (
                    time_first_sat if time_first_sat is not None else ""
                ),
                "time_latest_sat": (
                    time_latest_sat if time_latest_sat is not None else ""
                ),
                "time_unsat": time_unsat if time_unsat is not None else "",
                "best_bound": best_makespan if time_unsat is not None else "",
                "gap": 0.0 if verified and time_unsat is not None else "",
                "iterations": iterations,
            }
        )
        return SolveResult(
            instance=instance,
            data=data,
            starts=best_starts,
        )
    finally:
        solver.delete()
