from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cplex_mip import ExpandedEntity, SuboperationKey, expand_jssppr_entities
from .model import Domains, Instance


class CplexCpUnavailableError(RuntimeError):
    pass


@dataclass
class CplexCpModel:
    model: Any
    operation_intervals: Dict[int, Any]
    makespan: Any
    horizon: int
    interval_count: int
    constraint_count: int


@dataclass(frozen=True)
class CplexCpPlan:
    entities: Tuple[ExpandedEntity, ...]
    real_entities: Tuple[ExpandedEntity, ...]
    dummy_entities: Tuple[ExpandedEntity, ...]
    maximum_power: int
    real_phase_count: int


def load_docplex_symbols() -> Dict[str, Any]:
    try:
        from docplex.cp.expression import interval_var
        from docplex.cp.model import CpoModel
        from docplex.cp.modeler import (
            always_in,
            end_before_start,
            end_of,
            max_of,
            minimize,
            no_overlap,
            pulse,
            start_at_start,
            start_of,
        )
    except ImportError as error:
        raise CplexCpUnavailableError(
            "the CP Optimizer backend requires the 'docplex' package and a "
            "licensed IBM CP Optimizer runtime; install IBM CPLEX Optimization "
            "Studio and make its cpoptimizer executable discoverable, or set "
            "CPLEX_CP_EXECFILE in jssppr/config.py"
        ) from error

    return {
        "CpoModel": CpoModel,
        "interval_var": interval_var,
        "always_in": always_in,
        "end_before_start": end_before_start,
        "end_of": end_of,
        "max_of": max_of,
        "minimize": minimize,
        "no_overlap": no_overlap,
        "pulse": pulse,
        "start_at_start": start_at_start,
        "start_of": start_of,
    }


def prepare_cplex_cp_plan(instance: Instance, domains: Domains) -> CplexCpPlan:
    entities, maximum_power = expand_jssppr_entities(instance, domains)
    real_entities = tuple(entity for entity in entities if not entity.is_dummy)
    dummy_entities = tuple(entity for entity in entities if entity.is_dummy)
    return CplexCpPlan(
        entities=entities,
        real_entities=real_entities,
        dummy_entities=dummy_entities,
        maximum_power=maximum_power,
        real_phase_count=sum(
            len(entity.suboperations) for entity in real_entities
        ),
    )


def build_cplex_cp_model(
    instance: Instance,
    domains: Domains,
    warm_start: Optional[Sequence[Sequence[int]]] = None,
    model_name: str = "JSSPPR_CPLEX_CP",
) -> CplexCpModel:
    plan = prepare_cplex_cp_plan(instance, domains)
    symbols = load_docplex_symbols()
    model = symbols["CpoModel"](name=model_name)

    operation_intervals: Dict[int, Any] = {}
    phase_intervals: Dict[SuboperationKey, Any] = {}
    dummy_intervals: Dict[int, Any] = {}
    constraint_count = 0

    for entity in plan.real_entities:
        operation_id = int(entity.real_operation_id)
        master = symbols["interval_var"](
            start=(entity.first.start_lb, entity.first.start_ub),
            size=entity.duration,
            name="cp_op_j{}_o{}_id{}".format(
                entity.job_id, entity.job_position, operation_id
            ),
        )
        operation_intervals[operation_id] = master
        for phase in entity.suboperations:
            phase_interval = symbols["interval_var"](
                start=(phase.start_lb, phase.start_ub),
                size=phase.duration,
                name="cp_phase_e{}_p{}".format(
                    phase.entity_id, phase.phase_index
                ),
            )
            phase_intervals[phase.key] = phase_interval
            model.add(
                symbols["start_at_start"](
                    master, phase_interval, int(phase.offset)
                )
            )
            constraint_count += 1

    by_job: Dict[int, List[ExpandedEntity]] = {}
    for entity in plan.real_entities:
        by_job.setdefault(entity.job_id, []).append(entity)
    for job_entities in by_job.values():
        job_entities.sort(key=lambda item: int(item.job_position))
        for earlier_index, earlier in enumerate(job_entities):
            earlier_interval = operation_intervals[int(earlier.real_operation_id)]
            for later in job_entities[earlier_index + 1 :]:
                model.add(
                    symbols["end_before_start"](
                        earlier_interval,
                        operation_intervals[int(later.real_operation_id)],
                    )
                )
                constraint_count += 1

    by_machine: Dict[int, List[Any]] = {}
    for entity in plan.real_entities:
        by_machine.setdefault(int(entity.machine_id), []).append(
            operation_intervals[int(entity.real_operation_id)]
        )
    for machine_intervals in by_machine.values():
        if len(machine_intervals) > 1:
            model.add(symbols["no_overlap"](machine_intervals))
            constraint_count += 1

    power_terms: List[Any] = []
    for entity in plan.real_entities:
        for phase in entity.suboperations:
            if phase.power > plan.maximum_power:
                raise ValueError(
                    "suboperation e{} p{} requires power {}, above PTmax {}".format(
                        phase.entity_id,
                        phase.phase_index,
                        phase.power,
                        plan.maximum_power,
                    )
                )
            if phase.power > 0:
                power_terms.append(
                    symbols["pulse"](phase_intervals[phase.key], phase.power)
                )

    for entity in plan.dummy_entities:
        fixed_start = int(entity.fixed_start)
        phase = entity.first
        dummy = symbols["interval_var"](
            start=(fixed_start, fixed_start),
            size=entity.duration,
            name="cp_threshold_dummy_e{}".format(entity.entity_id),
        )
        dummy_intervals[entity.entity_id] = dummy
        model.add(symbols["start_of"](dummy) == fixed_start)
        constraint_count += 1
        if phase.power > 0:
            power_terms.append(symbols["pulse"](dummy, phase.power))

    if power_terms:
        power_usage = power_terms[0]
        for term in power_terms[1:]:
            power_usage = power_usage + term
        model.add(
            symbols["always_in"](
                power_usage,
                (0, instance.horizon),
                0,
                plan.maximum_power,
            )
        )
        constraint_count += 1

    makespan = symbols["max_of"](
        [
            symbols["end_of"](
                operation_intervals[int(entity.real_operation_id)]
            )
            for entity in plan.real_entities
        ]
    )
    model.add(symbols["minimize"](makespan))

    if warm_start is not None:
        flat = {
            operation.id: int(warm_start[job_index][operation_index])
            for job_index, job in enumerate(instance.jobs)
            for operation_index, operation in enumerate(job)
        }
        starting_point = model.create_empty_solution()
        for entity in plan.real_entities:
            operation_id = int(entity.real_operation_id)
            operation_start = flat[operation_id]
            starting_point.add_interval_var_solution(
                operation_intervals[operation_id],
                presence=True,
                start=operation_start,
                end=operation_start + entity.duration,
                size=entity.duration,
            )
            for phase in entity.suboperations:
                phase_start = operation_start + phase.offset
                starting_point.add_interval_var_solution(
                    phase_intervals[phase.key],
                    presence=True,
                    start=phase_start,
                    end=phase_start + phase.duration,
                    size=phase.duration,
                )
        for entity in plan.dummy_entities:
            dummy_start = int(entity.fixed_start)
            starting_point.add_interval_var_solution(
                dummy_intervals[entity.entity_id],
                presence=True,
                start=dummy_start,
                end=dummy_start + entity.duration,
                size=entity.duration,
            )
        model.set_starting_point(starting_point)

    return CplexCpModel(
        model=model,
        operation_intervals=operation_intervals,
        makespan=makespan,
        horizon=instance.horizon,
        interval_count=(
            len(operation_intervals) + len(phase_intervals) + len(dummy_intervals)
        ),
        constraint_count=constraint_count,
    )
