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


BACKENDS = ("sat", "cplex_cp", "cplex_mip", "gurobi")


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


HORIZONS = ("heuristic", "cache", "fixed", "safe", "dataset")

# Sentinel telling "--time-limit was not given" apart from "--time-limit none".
_UNSET = object()


def _time_limit(value: str) -> float | None:
    """Parse --time-limit; "none", "off" and 0 disable the limit."""
    if value.strip().lower() in ("none", "off", "no", "0"):
        return None
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number of seconds")
    if seconds <= 0:
        raise argparse.ArgumentTypeError("the time limit must be positive")
    return seconds


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m jssppr",
        description=(
            "Solve Job Shop Scheduling with Power Requirements (JSPPR) "
            "instances with SAT, CP Optimizer, CPLEX MILP or Gurobi. "
            "Every option defaults to the value set in jssppr/config.py."
        ),
    )
    parser.add_argument(
        "instances",
        nargs="*",
        help="instance files to solve; defaults to every .txt file in the dataset directory",
    )
    parser.add_argument(
        "-b",
        "--backend",
        choices=BACKENDS,
        default=None,
        help="solving backend",
    )
    parser.add_argument(
        "-s",
        "--solver",
        default=None,
        metavar="NAME",
        help="PySAT solver name used by the sat backend",
    )
    parser.add_argument(
        "-H",
        "--horizon",
        choices=HORIZONS,
        default=None,
        help=(
            "initial upper bound: heuristic runs Algorithm 1, cache reads "
            "the bound file, fixed uses --upper-bound, safe starts without a "
            "bound, dataset trusts the bound stored in the instance"
        ),
    )
    parser.add_argument(
        "-u",
        "--upper-bound",
        type=int,
        default=None,
        metavar="CMAX",
        help="fixed initial upper bound; implies --horizon fixed",
    )
    parser.add_argument(
        "-t",
        "--time-limit",
        type=_time_limit,
        default=_UNSET,
        metavar="SECONDS",
        help='per-instance wall-clock limit; "none" disables it',
    )
    parser.add_argument(
        "--heuristic-time-limit",
        type=float,
        default=None,
        metavar="SECONDS",
        help="time given to the upper-bound heuristic",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="random seed of the upper-bound heuristic",
    )
    preprocessing = parser.add_mutually_exclusive_group()
    preprocessing.add_argument(
        "--preprocess",
        dest="preprocess",
        action="store_true",
        default=None,
        help="restrict operation starts to precedence-based windows",
    )
    preprocessing.add_argument(
        "--no-preprocess",
        dest="preprocess",
        action="store_false",
        default=None,
        help="keep the full start-time domains",
    )
    parser.add_argument(
        "-d",
        "--dataset-dir",
        default=None,
        metavar="DIR",
        help="directory scanned when no instance is given",
    )
    parser.add_argument(
        "-o",
        "--results-dir",
        default=None,
        metavar="DIR",
        help="directory that receives the run directory",
    )
    parser.add_argument("--worker", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--run-directory", default=None, help=argparse.SUPPRESS)
    return parser


def _override_config(
    parser: argparse.ArgumentParser,
    arguments: argparse.Namespace,
) -> None:
    if arguments.upper_bound is not None:
        if arguments.upper_bound <= 0:
            parser.error("--upper-bound must be positive")
        if arguments.horizon not in (None, "fixed"):
            parser.error("--upper-bound cannot be combined with --horizon " + arguments.horizon)
        config.UPPER_BOUND = arguments.upper_bound
        config.HORIZON = "fixed"
    elif arguments.horizon is not None:
        if arguments.horizon == "fixed" and config.UPPER_BOUND is None:
            parser.error("--horizon fixed requires --upper-bound")
        config.HORIZON = arguments.horizon

    if arguments.backend is not None:
        config.BACKEND = arguments.backend
    if arguments.solver is not None:
        config.SOLVER = arguments.solver
    if arguments.time_limit is not _UNSET:
        config.TIME_LIMIT_SECONDS = arguments.time_limit
    if arguments.heuristic_time_limit is not None:
        if arguments.heuristic_time_limit < 0:
            parser.error("--heuristic-time-limit cannot be negative")
        config.HEURISTIC_TIME_LIMIT = arguments.heuristic_time_limit
    if arguments.seed is not None:
        config.HEURISTIC_SEED = arguments.seed
    if arguments.preprocess is not None:
        config.PREPROCESS = arguments.preprocess
    if arguments.dataset_dir is not None:
        config.DATASET_DIR = Path(arguments.dataset_dir)


def main() -> int:
    parser = _build_parser()
    arguments = parser.parse_args()
    if arguments.worker:
        if not arguments.run_directory:
            parser.error("--worker requires --run-directory")
        return _worker(arguments.worker, arguments.run_directory)
    _override_config(parser, arguments)
    return run(arguments.instances or None, arguments.results_dir)
