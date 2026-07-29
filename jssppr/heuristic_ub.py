from __future__ import annotations

import random
import time
from typing import List

from .model import Instance


def _construct(
    instance: Instance,
    generator: random.Random,
) -> List[List[int]] | None:
    job_progress = [0] * instance.n_jobs
    job_ready = [0] * instance.n_jobs
    machine_ready = [0] * instance.n_machines
    starts = [[-1] * len(job) for job in instance.jobs]
    power_used = [0] * instance.horizon
    remaining = len(instance.operations)

    while remaining:
        ready_jobs = [
            job
            for job in range(instance.n_jobs)
            if job_progress[job] < len(instance.jobs[job])
        ]
        job = generator.choice(ready_jobs)
        operation_index = job_progress[job]
        operation = instance.jobs[job][operation_index]
        start = max(job_ready[job], machine_ready[operation.machine])
        selected = None

        while start + operation.duration <= instance.horizon:
            feasible = True
            for offset in range(operation.duration):
                time_index = start + offset
                required = operation.nominal_power
                if offset < operation.peak_duration:
                    required += operation.peak_power
                if (
                    power_used[time_index] + required
                    > instance.power_thresholds[time_index]
                ):
                    feasible = False
                    break
            if feasible:
                selected = start
                break
            start += 1

        if selected is None:
            return None

        starts[job][operation_index] = selected
        for offset in range(operation.duration):
            time_index = selected + offset
            power_used[time_index] += operation.nominal_power
            if offset < operation.peak_duration:
                power_used[time_index] += operation.peak_power

        end = selected + operation.duration
        job_ready[job] = end
        machine_ready[operation.machine] = end
        job_progress[job] += 1
        remaining -= 1

    return starts


def find_upper_bound(
    instance: Instance,
    time_limit: float,
    seed: int,
) -> tuple[int | None, List[List[int]] | None, int]:
    generator = random.Random(seed)
    deadline = time.perf_counter() + max(0.0, time_limit)
    best_makespan = None
    best_starts = None
    iterations = 0

    while iterations == 0 or time.perf_counter() < deadline:
        iterations += 1
        starts = _construct(instance, generator)
        if starts is None:
            continue
        makespan = max(
            starts[job_index][-1] + job[-1].duration
            for job_index, job in enumerate(instance.jobs)
        )
        if best_makespan is None or makespan < best_makespan:
            best_makespan = makespan
            best_starts = starts

    return best_makespan, best_starts, iterations
