from __future__ import annotations

from .encoding_common import ClauseCounter, Variables, add_power_bound
from .model import Instance


def add_state_constraints(
    instance: Instance,
    variables: Variables,
    clauses: ClauseCounter,
) -> None:
    for operation in instance.operations:
        earliest = variables.domains.earliest[operation.id]
        latest = variables.domains.latest[operation.id]
        for start in range(earliest, latest + 1):
            not_start = variables.start(operation.id, start, negative=True)
            for offset in range(operation.peak_duration):
                clauses.add(
                    [
                        not_start,
                        variables.state("peak", operation.id, start + offset),
                    ]
                )
            for offset in range(operation.peak_duration, operation.duration):
                clauses.add(
                    [
                        not_start,
                        variables.state("base", operation.id, start + offset),
                    ]
                )


def add_extra_constraints(
    instance: Instance,
    variables: Variables,
    clauses: ClauseCounter,
) -> None:
    for operation in instance.operations:
        earliest = variables.domains.earliest[operation.id]
        latest = variables.domains.latest[operation.id]
        for time in range(earliest, latest + 2):
            order = variables.order(operation.id, time)
            not_order = variables.order(operation.id, time, negative=True)
            if operation.peak_duration > 0:
                clauses.add(
                    [
                        not_order,
                        variables.state("peak", operation.id, time - 1, negative=True),
                    ]
                )
                clauses.add(
                    [
                        order,
                        variables.state(
                            "peak",
                            operation.id,
                            time + operation.peak_duration - 1,
                            negative=True,
                        ),
                    ]
                )
            if operation.duration > operation.peak_duration:
                clauses.add(
                    [
                        not_order,
                        variables.state(
                            "base",
                            operation.id,
                            time + operation.peak_duration - 1,
                            negative=True,
                        ),
                    ]
                )
                clauses.add(
                    [
                        order,
                        variables.state(
                            "base",
                            operation.id,
                            time + operation.duration - 1,
                            negative=True,
                        ),
                    ]
                )


def add_power_constraints(
    instance: Instance,
    variables: Variables,
    clauses: ClauseCounter,
) -> None:
    for time in range(instance.horizon):
        literals = []
        weights = []
        for operation in instance.operations:
            earliest = variables.domains.earliest[operation.id]
            latest = variables.domains.latest[operation.id]
            if (
                operation.peak_duration > 0
                and operation.nominal_power + operation.peak_power > 0
                and earliest <= time <= latest + operation.peak_duration - 1
            ):
                literals.append(variables.state("peak", operation.id, time))
                weights.append(operation.nominal_power + operation.peak_power)
            if (
                operation.duration > operation.peak_duration
                and operation.nominal_power > 0
                and earliest + operation.peak_duration
                <= time
                <= latest + operation.duration - 1
            ):
                literals.append(variables.state("base", operation.id, time))
                weights.append(operation.nominal_power)
        add_power_bound(
            variables,
            clauses,
            literals,
            weights,
            instance.power_thresholds[time],
        )


def apply(
    instance: Instance,
    variables: Variables,
    clauses: ClauseCounter,
) -> None:
    add_state_constraints(instance, variables, clauses)
    add_power_constraints(instance, variables, clauses)
