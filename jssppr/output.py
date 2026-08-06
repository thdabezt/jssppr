from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List

from .model import SolveResult


SUMMARY_FIELDS = [
    "instance",
    "backend",
    "jobs",
    "machines",
    "operations",
    "solver",
    "pb_encoding_used",
    "vars",
    "clauses",
    "solve_time",
    "time_first_sat",
    "time_latest_sat",
    "time_unsat",
    "UB",
    "horizon_mode",
    "makespan",
    "optimal",
    "best_bound",
    "gap",
    "status",
]


def _operations(result: SolveResult) -> List[Dict[str, int]]:
    if result.starts is None:
        return []
    rows = []
    for job_index, job in enumerate(result.instance.jobs):
        for operation_index, operation in enumerate(job):
            start = result.starts[job_index][operation_index]
            rows.append(
                {
                    "id": operation.id,
                    "job": job_index,
                    "operation": operation_index,
                    "machine": operation.machine,
                    "start": start,
                    "duration": operation.duration,
                    "end": start + operation.duration,
                    "nominal_power": operation.nominal_power,
                    "peak_power": operation.peak_power,
                    "peak_duration": operation.peak_duration,
                }
            )
    return rows


def write_result(run_directory: Path, result: SolveResult) -> Dict[str, object]:
    instance_directory = run_directory / Path(result.instance.name).stem
    instance_directory.mkdir(parents=True, exist_ok=True)

    data = dict(result.data)
    data["result_directory"] = str(instance_directory)
    (instance_directory / "result.json").write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )

    data_lines = [
        f"instance={data['instance']}",
        f"status={data['status']}",
        f"backend={data['backend']}",
        f"jobs={data['jobs']}",
        f"machines={data['machines']}",
        f"operations={data['operations']}",
        f"solver={data['solver']}",
        f"pb_encoding_used={data['pb_encoding_used']}",
        f"UB={data['UB']}",
        f"best_makespan={data['makespan'] if data['makespan'] is not None else ''}",
        f"optimal={data['optimal']}",
        f"best_bound={data['best_bound']}",
        f"gap={data['gap']}",
        f"vars={data['vars']}",
        f"clauses={data['clauses']}",
        f"time_total={data['solve_time']}",
        f"time_solve={data['solve_time']}",
        f"time_first_sat={data['time_first_sat']}",
        f"time_latest_sat={data['time_latest_sat']}",
        f"time_unsat={data['time_unsat']}",
    ]
    (instance_directory / "data.txt").write_text(
        "\n".join(data_lines) + "\n",
        encoding="utf-8",
    )

    log_lines = []
    for iteration in data.get("iterations", []):
        if "bound" in iteration:
            if iteration["status"] == "SAT":
                log_lines.append(
                    f"SAT bound={iteration['bound']} "
                    f"makespan={iteration['makespan']} "
                    f"iteration_time={iteration['iteration_time']:.6f} "
                    f"elapsed={iteration['elapsed']:.6f}"
                )
            else:
                log_lines.append(
                    f"UNSAT bound={iteration['bound']} "
                    f"proof_time={iteration['iteration_time']:.6f} "
                    f"elapsed={iteration['elapsed']:.6f}"
                )
        else:
            log_lines.append(
                f"SOLUTION {iteration['solution']} "
                f"objective={iteration['objective']} "
                f"elapsed={iteration['elapsed']:.6f}"
            )
    (instance_directory / "log.txt").write_text(
        "\n".join(log_lines) + ("\n" if log_lines else ""),
        encoding="utf-8",
    )

    if result.starts is not None:
        raw = {
            "instance": result.instance.name,
            "backend": data["backend"],
            "horizon": result.instance.horizon,
            "makespan": data["makespan"],
            "starts": result.starts,
            "operations": _operations(result),
        }
        (instance_directory / "raw_schedule.json").write_text(
            json.dumps(raw, indent=2),
            encoding="utf-8",
        )

        lines = []
        for job_index, job in enumerate(result.instance.jobs):
            entries = []
            for operation_index, operation in enumerate(job):
                start = result.starts[job_index][operation_index]
                entries.append(
                    f"O{operation_index}:M{operation.machine},"
                    f"start={start},duration={operation.duration},"
                    f"end={start + operation.duration}"
                )
            lines.append(f"Job {job_index}: " + " | ".join(entries))
        (instance_directory / "schedule.txt").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    return data


def write_error(run_directory: Path, source: Path, error: Exception) -> Dict[str, object]:
    instance_directory = run_directory / source.stem
    instance_directory.mkdir(parents=True, exist_ok=True)
    data = {
        "instance": source.name,
        "status": "ERROR",
        "error": f"{type(error).__name__}: {error}",
        "result_directory": str(instance_directory),
    }
    (instance_directory / "result.json").write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )
    return data


def write_summary(run_directory: Path, rows: Iterable[Dict[str, object]]) -> Path:
    path = run_directory / "summary.csv"
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=SUMMARY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in SUMMARY_FIELDS})
    return path
