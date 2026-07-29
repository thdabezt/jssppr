from __future__ import annotations

from .encoding1 import (
    add_extra_constraints,
    add_power_constraints,
    add_state_constraints,
)
from .encoding_common import ClauseCounter, Variables
from .model import Instance


def apply(
    instance: Instance,
    variables: Variables,
    clauses: ClauseCounter,
) -> None:
    add_state_constraints(instance, variables, clauses)
    add_extra_constraints(instance, variables, clauses)
    add_power_constraints(instance, variables, clauses)
