from __future__ import annotations

from .encoding_common import ClauseCounter, Variables, add_power_bound
from .model import Instance


def apply(
    instance: Instance,
    variables: Variables,
    clauses: ClauseCounter,
) -> None:
    for operation in instance.operations:
        earliest = variables.domains.earliest[operation.id]
        latest = variables.domains.latest[operation.id]
        for start in range(earliest, latest + 1):
            not_start = variables.start(operation.id, start, negative=True)
            if operation.nominal_power > 0:
                for offset in range(operation.duration):
                    clauses.add(
                        [
                            not_start,
                            variables.state("active", operation.id, start + offset),
                        ]
                    )
            if operation.peak_power > 0:
                for offset in range(operation.peak_duration):
                    clauses.add(
                        [
                            not_start,
                            variables.state("initial", operation.id, start + offset),
                        ]
                    )

    for time in range(instance.horizon):
        literals = []
        weights = []
        for operation in instance.operations:
            earliest = variables.domains.earliest[operation.id]
            latest = variables.domains.latest[operation.id]
            if (
                operation.nominal_power > 0
                and earliest <= time <= latest + operation.duration - 1
            ):
                literals.append(variables.state("active", operation.id, time))
                weights.append(operation.nominal_power)
            if (
                operation.peak_power > 0
                and operation.peak_duration > 0
                and earliest <= time <= latest + operation.peak_duration - 1
            ):
                literals.append(variables.state("initial", operation.id, time))
                weights.append(operation.peak_power)
        add_power_bound(
            variables,
            clauses,
            literals,
            weights,
            instance.power_thresholds[time],
        )
