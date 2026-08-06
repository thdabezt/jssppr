from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List

from . import config
from .model import SolveResult
from .output import write_error, write_result, write_summary


BACKENDS = ("sat", "cplex_cp1", "cplex_cp2", "cplex_mip", "gurobi")


def _backend() -> Callable[[str | Path], SolveResult]:
    backend = str(config.BACKEND).lower()
    if backend not in BACKENDS:
        raise ValueError(
            "unsupported backend {!r}; choose one of: {}".format(
                config.BACKEND, ", ".join(BACKENDS)
            )
        )
    if backend == "sat":
        from .solver import solve_instance

        return solve_instance
    if backend == "cplex_mip":
        from .cplex_mip_solver import solve_instance

        return solve_instance
    if backend == "gurobi":
        from .gurobi_mip_solver import solve_instance

        return solve_instance
    from .cplex_cp_solver import solve_instance

    return solve_instance


def _instances(paths: Iterable[str | Path] | None) -> List[Path]:
    if paths:
        candidates = [Path(path).resolve() for path in paths]
    else:
        dataset = Path(config.DATASET_DIR)
        candidates = sorted(
            (path.resolve() for path in dataset.glob("*.txt") if path.is_file()),
            key=lambda path: path.name.casefold(),
        )

    missing = [path for path in candidates if not path.is_file()]
    if missing:
        raise FileNotFoundError(", ".join(str(path) for path in missing))
    return candidates


def _needs_worker() -> bool:
    return (
        str(config.BACKEND).lower() == "sat"
        and config.TIME_LIMIT_SECONDS is not None
    )


CONFIG_ENVIRONMENT = "JSSPPR_CONFIG"


def _config_payload() -> str:
    payload: Dict[str, object] = {}
    for name in dir(config):
        if not name.isupper():
            continue
        value = getattr(config, name)
        if isinstance(value, Path):
            payload[name] = str(value)
        elif value is None or isinstance(value, (str, int, float, bool)):
            payload[name] = value
    return json.dumps(payload)


def _apply_config(payload: str) -> None:
    for name, value in json.loads(payload).items():
        if hasattr(config, name):
            setattr(config, name, value)


def _timed_out_row(
    run_directory: Path,
    source: Path,
    limit: float,
) -> Dict[str, object]:
    result_path = run_directory / source.stem / "result.json"
    if result_path.is_file():
        data = json.loads(result_path.read_text(encoding="utf-8"))
        data["status"] = "OK" if data.get("makespan") is not None else "TIMEOUT"
        data["optimal"] = False
        data["stop_cause"] = "time limit"
        data["solve_time"] = limit
        result_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
    return {
        "instance": source.name,
        "backend": str(config.BACKEND).lower(),
        "status": "TIMEOUT",
        "optimal": False,
        "makespan": None,
        "solve_time": limit,
        "stop_cause": "time limit",
    }


def _run_worker(
    run_directory: Path,
    source: Path,
    limit: float,
) -> Dict[str, object]:
    command = [
        sys.executable,
        "-m",
        "jssppr",
        "--worker",
        str(source),
        "--run-directory",
        str(run_directory),
    ]
    environment = dict(os.environ)
    environment[CONFIG_ENVIRONMENT] = _config_payload()
    process = subprocess.Popen(
        command,
        cwd=str(config.PROJECT_ROOT),
        env=environment,
    )
    try:
        process.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        print(f"[{source.name}] stopped at the {limit:g}s limit")
        return _timed_out_row(run_directory, source, limit)

    result_path = run_directory / source.stem / "result.json"
    if not result_path.is_file():
        raise RuntimeError(
            f"worker for {source.name} exited with {process.returncode} "
            f"and wrote no result"
        )
    return json.loads(result_path.read_text(encoding="utf-8"))


def run(
    paths: Iterable[str | Path] | None = None,
    results_directory: str | Path | None = None,
) -> int:
    solve_instance = _backend()
    inputs = _instances(paths)
    if not inputs:
        print(f"No .txt instances found in {Path(config.DATASET_DIR)}")
        return 0

    root = Path(results_directory) if results_directory else Path(config.RESULTS_DIR)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_directory = root.resolve() / f"run_{timestamp}_{str(config.BACKEND).lower()}"
    run_directory.mkdir(parents=True, exist_ok=False)

    limit = float(config.TIME_LIMIT_SECONDS) if _needs_worker() else None

    rows = []
    errors = 0
    for source in inputs:
        print(f"[{source.name}] solving with {str(config.BACKEND).lower()}")
        try:
            if limit is not None:
                row = _run_worker(run_directory, source, limit)
            else:
                row = write_result(run_directory, solve_instance(source))
            print(
                f"[{source.name}] {row['status']} "
                f"makespan={row.get('makespan')} "
                f"verified={row.get('verified')}"
            )
        except Exception as error:
            errors += 1
            row = write_error(run_directory, source, error)
            print(f"[{source.name}] {row['error']}")
        rows.append(row)
        write_summary(run_directory, rows)

    summary = write_summary(run_directory, rows)
    print(f"Summary: {summary}")
    return 1 if errors else 0


def _worker(source: str, run_directory: str) -> int:
    payload = os.environ.get(CONFIG_ENVIRONMENT)
    if payload:
        _apply_config(payload)

    from .solver import solve_instance

    directory = Path(run_directory)

    def checkpoint(result: SolveResult) -> None:
        write_result(directory, result)

    write_result(directory, solve_instance(source, checkpoint=checkpoint))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m jssppr")
    parser.add_argument("instances", nargs="*")
    parser.add_argument("--worker", default=None)
    parser.add_argument("--run-directory", default=None)
    arguments = parser.parse_args()
    if arguments.worker:
        if not arguments.run_directory:
            parser.error("--worker requires --run-directory")
        return _worker(arguments.worker, arguments.run_directory)
    return run(arguments.instances or None)
