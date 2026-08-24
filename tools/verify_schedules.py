from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from jssppr.parser import read_instance  # noqa: E402


def read_out(path: Path) -> Dict[int, Dict[int, int]]:
    """Read a .out file into {job index: {operation index: start}}, 0-based."""
    starts: Dict[int, Dict[int, int]] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line[0].isalpha():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError(f"{path.name} line {number}: expected four values")
        job, operation, start, _end = (int(value) for value in fields)
        starts.setdefault(job - 1, {})[operation - 1] = start
    if not starts:
        raise ValueError(f"{path.name}: no schedule lines")
    return starts


def check(instance, starts: Dict[int, Dict[int, int]]) -> Tuple[Optional[int], str]:
    """Return (makespan, "") when feasible, otherwise (makespan, reason)."""
    if len(starts) != instance.n_jobs:
        return None, f"schedule covers {len(starts)} of {instance.n_jobs} jobs"

    horizon = 0
    placed = []
    for job_index, job in enumerate(instance.jobs):
        rows = starts.get(job_index, {})
        if len(rows) != len(job):
            return None, f"job {job_index + 1} has {len(rows)} of {len(job)} operations"
        for operation_index, operation in enumerate(job):
            start = rows[operation_index]
            if start < 0:
                return None, f"job {job_index + 1} operation {operation_index + 1} starts before 0"
            placed.append((job_index, operation_index, operation, start))
            horizon = max(horizon, start + operation.duration)

    makespan = horizon

    for job_index, job in enumerate(instance.jobs):
        previous_end = 0
        for operation_index, operation in enumerate(job):
            start = starts[job_index][operation_index]
            if start < previous_end:
                return makespan, (
                    f"precedence: job {job_index + 1} operation "
                    f"{operation_index + 1} starts at {start} before {previous_end}"
                )
            previous_end = start + operation.duration

    machines: Dict[int, List[Tuple[int, int, int, int]]] = {}
    for job_index, operation_index, operation, start in placed:
        machines.setdefault(operation.machine, []).append(
            (start, start + operation.duration, job_index, operation_index)
        )
    for machine, intervals in sorted(machines.items()):
        intervals.sort()
        for left, right in zip(intervals, intervals[1:]):
            if left[1] > right[0]:
                return makespan, (
                    f"machine {machine}: job {left[2] + 1} operation {left[3] + 1} "
                    f"overlaps job {right[2] + 1} operation {right[3] + 1} at {right[0]}"
                )

    power = [0] * (horizon + 1)
    for _job_index, _operation_index, operation, start in placed:
        for offset in range(operation.duration):
            demand = operation.nominal_power
            if offset < operation.peak_duration:
                demand += operation.peak_power
            power[start + offset] += demand

    thresholds = instance.power_thresholds
    worst = None
    violations = 0
    for time_index in range(horizon):
        if time_index >= len(thresholds):
            return makespan, f"power profile ends at {len(thresholds) - 1}, before {time_index}"
        overage = power[time_index] - thresholds[time_index]
        if overage > 0:
            violations += 1
            if worst is None or overage > worst[0]:
                worst = (overage, time_index, power[time_index], thresholds[time_index])
    if worst is not None:
        _overage, time_index, demand, threshold = worst
        return makespan, (
            f"power: {violations} violating time steps, worst at "
            f"t={time_index} P(t)={demand} PT(t)={threshold}"
        )
    return makespan, ""


def profile(instance, starts: Dict[int, Dict[int, int]]) -> Tuple[List[int], List[int], int]:
    """Return (P(t), PT(t), horizon) for a schedule."""
    horizon = 0
    for job_index, job in enumerate(instance.jobs):
        for operation_index, operation in enumerate(job):
            horizon = max(horizon, starts[job_index][operation_index] + operation.duration)
    power = [0] * horizon
    for job_index, job in enumerate(instance.jobs):
        for operation_index, operation in enumerate(job):
            start = starts[job_index][operation_index]
            for offset in range(operation.duration):
                demand = operation.nominal_power
                if offset < operation.peak_duration:
                    demand += operation.peak_power
                power[start + offset] += demand
    thresholds = list(instance.power_thresholds[:horizon])
    return power, thresholds, horizon


def plot(instance, starts: Dict[int, Dict[int, int]], name: str, path: Path) -> None:
    """Draw the machine occupancy and the power profile against the threshold."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as pyplot
    except ImportError:  # pragma: no cover - depends on the environment
        raise SystemExit("--figures needs matplotlib; install it with pip install matplotlib")

    power, thresholds, horizon = profile(instance, starts)
    figure, (gantt, curve) = pyplot.subplots(
        2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [1.2, 1]}
    )

    for job_index, job in enumerate(instance.jobs):
        for operation_index, operation in enumerate(job):
            start = starts[job_index][operation_index]
            gantt.broken_barh(
                [(start, operation.duration)],
                (operation.machine - 0.4, 0.8),
                facecolors="#a8d5e5",
                edgecolors="black",
                linewidth=0.8,
            )
            gantt.text(
                start + operation.duration / 2,
                operation.machine,
                f"J{job_index + 1}O{operation_index + 1}",
                ha="center",
                va="center",
                fontsize=7,
            )
    gantt.set_yticks(range(instance.n_machines))
    gantt.set_yticklabels([f"Mach {machine}" for machine in range(instance.n_machines)])
    gantt.set_xlim(0, horizon)
    gantt.set_xlabel("Time")
    gantt.set_title(f"Machine occupancy: {name}")
    gantt.grid(axis="x", linestyle="--", alpha=0.4)

    times = range(horizon)
    curve.fill_between(times, power, step="post", color="#f5b942", label="Consumed power")
    curve.step(times, thresholds, where="post", color="blue", linewidth=1.6, label="Power threshold")
    first = True
    for time_index in times:
        if power[time_index] > thresholds[time_index]:
            curve.axvspan(
                time_index,
                time_index + 1,
                color="red",
                alpha=0.35,
                label="Violation" if first else None,
            )
            first = False
    curve.set_xlim(0, horizon)
    curve.set_xlabel("Time")
    curve.set_ylabel("Power")
    curve.set_title("Power consumption vs threshold")
    curve.grid(linestyle="--", alpha=0.4)
    curve.legend(loc="upper right")

    figure.tight_layout()
    figure.savefig(path, dpi=150)
    pyplot.close(figure)


def collect(paths: List[str]) -> List[Path]:
    files: List[Path] = []
    for entry in paths:
        path = Path(entry)
        if path.is_dir():
            files.extend(sorted(path.glob("*.out"), key=lambda item: item.name.casefold()))
        elif path.is_file():
            files.append(path)
        else:
            raise SystemExit(f"not found: {path}")
    if not files:
        raise SystemExit("no .out files given")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(prog="python tools/verify_schedules.py")
    parser.add_argument("paths", nargs="+", help=".out files or directories of them")
    parser.add_argument(
        "--dataset",
        default=str(PROJECT_ROOT / "dataset"),
        help="directory holding the instance files",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any schedule is infeasible",
    )
    parser.add_argument(
        "--figures",
        default=None,
        metavar="DIR",
        help="also write a machine-occupancy and power plot per infeasible schedule",
    )
    parser.add_argument(
        "--figures-all",
        action="store_true",
        help="with --figures, plot every schedule instead of only the infeasible ones",
    )
    arguments = parser.parse_args()

    dataset = Path(arguments.dataset)
    figures = Path(arguments.figures) if arguments.figures else None
    if figures is not None:
        figures.mkdir(parents=True, exist_ok=True)
    drawn = 0
    feasible = 0
    failures = []
    for path in collect(arguments.paths):
        name = path.stem
        source = dataset / f"{name}.txt"
        if not source.is_file():
            failures.append((name, f"no instance file at {source}"))
            print(f"{name:15s}       ERROR   no instance file")
            continue
        instance = read_instance(source)
        starts = read_out(path)
        makespan, reason = check(instance, starts)
        shown = "-" if makespan is None else str(makespan)
        if figures is not None and makespan is not None and (reason or arguments.figures_all):
            plot(instance, starts, name, figures / f"{name}.png")
            drawn += 1
        if reason:
            failures.append((name, reason))
            print(f"{name:15s} {shown:>5s} INVALID  {reason}")
        else:
            feasible += 1
            print(f"{name:15s} {shown:>5s} feasible")

    total = feasible + len(failures)
    print(f"\n{feasible}/{total} schedules feasible")
    if failures:
        print(f"{len(failures)} infeasible:")
        for name, reason in failures:
            print(f"  {name}: {reason}")
    return 1 if failures and arguments.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
