from __future__ import annotations

from typing import Dict, Iterable, List

from pysat.formula import IDPool
from pysat.pb import EncType as PBEncoding
from pysat.pb import PBEnc

from .model import Domains, Instance, Operation


try:
    __import__("pypblib.pblib")
except ImportError as error:
    raise ImportError(
        "the binary-merge pseudo-Boolean encoding requires pypblib; "
        "install it with `pip install pypblib`"
    ) from error

POWER_BOUND_ENCODING = "binmerge"


class ClauseCounter:
    def __init__(self, solver) -> None:
        self.solver = solver
        self.count = 0

    def add(self, literals: Iterable[int | bool]) -> None:
        clause: List[int] = []
        for literal in literals:
            if literal is True:
                return
            if literal is False:
                continue
            clause.append(int(literal))
        self.solver.add_clause(clause)
        self.count += 1

    def add_many(self, clauses: Iterable[Iterable[int]]) -> None:
        for clause in clauses:
            self.solver.add_clause(list(clause))
            self.count += 1


class Variables:
    def __init__(self, domains: Domains, horizon: int) -> None:
        self.domains = domains
        self.horizon = horizon
        self.pool = IDPool()

    def order(self, operation: int, time: int, negative: bool = False) -> int | bool:
        earliest = self.domains.earliest[operation]
        latest = self.domains.latest[operation]
        if time <= earliest:
            return False if negative else True
        if time > latest:
            return True if negative else False
        variable = self.pool.id(("order", operation, time))
        return -variable if negative else variable

    def start(self, operation: int, time: int, negative: bool = False) -> int | bool:
        earliest = self.domains.earliest[operation]
        latest = self.domains.latest[operation]
        if time < earliest or time > latest:
            return True if negative else False
        variable = self.pool.id(("start", operation, time))
        return -variable if negative else variable

    def state(
        self,
        name: str,
        operation: int,
        time: int,
        negative: bool = False,
    ) -> int | bool:
        if time < 0 or time >= self.horizon:
            return True if negative else False
        variable = self.pool.id(("state", name, operation, time))
        return -variable if negative else variable


def machine_groups(instance: Instance) -> Dict[int, List[Operation]]:
    groups = {machine: [] for machine in range(instance.n_machines)}
    for operation in instance.operations:
        groups[operation.machine].append(operation)
    return groups


def add_base_constraints(
    instance: Instance,
    variables: Variables,
    clauses: ClauseCounter,
) -> None:
    for operation in instance.operations:
        earliest = variables.domains.earliest[operation.id]
        latest = variables.domains.latest[operation.id]
        clauses.add([variables.order(operation.id, earliest)])
        clauses.add([variables.order(operation.id, latest + 1, negative=True)])

        for time in range(earliest, latest + 1):
            clauses.add(
                [
                    variables.order(operation.id, time + 1, negative=True),
                    variables.order(operation.id, time),
                ]
            )
            clauses.add(
                [
                    variables.start(operation.id, time, negative=True),
                    variables.order(operation.id, time),
                ]
            )
            clauses.add(
                [
                    variables.start(operation.id, time, negative=True),
                    variables.order(operation.id, time + 1, negative=True),
                ]
            )
            clauses.add(
                [
                    variables.order(operation.id, time, negative=True),
                    variables.order(operation.id, time + 1),
                    variables.start(operation.id, time),
                ]
            )

    for job in instance.jobs:
        for operation, following in zip(job, job[1:]):
            earliest = variables.domains.earliest[operation.id]
            latest = variables.domains.latest[operation.id]
            for time in range(earliest, latest + 1):
                clauses.add(
                    [
                        variables.start(operation.id, time, negative=True),
                        variables.order(following.id, time + operation.duration),
                    ]
                )

    for operations in machine_groups(instance).values():
        for left_index in range(len(operations)):
            left = operations[left_index]
            for right in operations[left_index + 1 :]:
                left_earliest = variables.domains.earliest[left.id]
                left_latest = variables.domains.latest[left.id]
                for time in range(left_earliest, left_latest + 1):
                    clauses.add(
                        [
                            variables.start(left.id, time, negative=True),
                            variables.order(right.id, time + left.duration),
                            variables.order(right.id, time - right.duration + 1, negative=True),
                        ]
                    )

                right_earliest = variables.domains.earliest[right.id]
                right_latest = variables.domains.latest[right.id]
                for time in range(right_earliest, right_latest + 1):
                    clauses.add(
                        [
                            variables.start(right.id, time, negative=True),
                            variables.order(left.id, time + right.duration),
                            variables.order(left.id, time - left.duration + 1, negative=True),
                        ]
                    )


def add_makespan_bound(
    instance: Instance,
    variables: Variables,
    clauses: ClauseCounter,
    bound: int,
) -> None:
    for job in instance.jobs:
        operation = job[-1]
        clauses.add(
            [
                variables.order(
                    operation.id,
                    bound - operation.duration + 1,
                    negative=True,
                )
            ]
        )


def add_power_bound(
    variables: Variables,
    clauses: ClauseCounter,
    literals: List[int],
    weights: List[int],
    bound: int,
) -> None:
    if not literals:
        return
    if len(literals) != len(weights):
        raise ValueError("power-bound literals and weights must have equal length")

    formula = PBEnc.leq(
        lits=literals,
        weights=weights,
        bound=bound,
        top_id=variables.pool.top,
        encoding=PBEncoding.binmerge,
    )
    clauses.add_many(formula.clauses)
    variables.pool.top = max(variables.pool.top, formula.nv)
