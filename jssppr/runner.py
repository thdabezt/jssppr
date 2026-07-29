from __future__ import annotations

import argparse
import datetime
from pathlib import Path
from typing import Iterable, List

from . import config
from .output import write_error, write_result, write_summary
from .solver import solve_instance


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


def run(
    paths: Iterable[str | Path] | None = None,
    results_directory: str | Path | None = None,
) -> int:
    inputs = _instances(paths)
    if not inputs:
        print(f"No .txt instances found in {Path(config.DATASET_DIR)}")
        return 0

    root = Path(results_directory) if results_directory else Path(config.RESULTS_DIR)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_directory = root.resolve() / f"run_{timestamp}"
    run_directory.mkdir(parents=True, exist_ok=False)

    rows = []
    errors = 0
    for source in inputs:
        print(f"[{source.name}] solving")
        try:
            result = solve_instance(source)
            row = write_result(run_directory, result)
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


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m jssppr")
    parser.add_argument("instances", nargs="*")
    arguments = parser.parse_args()
    return run(arguments.instances or None)
