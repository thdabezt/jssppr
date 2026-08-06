from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .cplex_cp import compress_power_thresholds, effective_peak_duration
from .model import Domains, Instance


SuboperationKey = Tuple[int, int]
MachineOrderKey = Tuple[int, int]
FlowArcKey = Tuple[Optional[SuboperationKey], SuboperationKey]


class CplexMipUnavailableError(RuntimeError):
    pass


class CplexMipHorizonInfeasibleError(ValueError):
    pass


@dataclass(frozen=True)
class ExpandedSuboperation:
    key: SuboperationKey
    entity_id: int
    phase_index: int
    offset: int
    duration: int
    power: int
    start_lb: int
    start_ub: int
    is_dummy: bool
    real_operation_id: Optional[int]

    @property
    def earliest_end(self) -> int:
        return self.start_lb + self.duration

    @property
    def latest_end(self) -> int:
        return self.start_ub + self.duration


@dataclass(frozen=True)
class ExpandedEntity:
    entity_id: int
    job_id: int
    machine_id: Optional[int]
    job_position: Optional[int]
    is_dummy: bool
    real_operation_id: Optional[int]
    fixed_start: Optional[int]
    suboperations: Tuple[ExpandedSuboperation, ...]

    @property
    def first(self) -> ExpandedSuboperation:
        return self.suboperations[0]

    @property
    def last(self) -> ExpandedSuboperation:
        return self.suboperations[-1]

    @property
    def duration(self) -> int:
        return sum(suboperation.duration for suboperation in self.suboperations)


@dataclass
class CplexMipModel:
    model: Any
    entities: Tuple[ExpandedEntity, ...]
    suboperations: Dict[SuboperationKey, ExpandedSuboperation]
    start_variables: Dict[int, Any]
    suboperation_starts: Dict[SuboperationKey, Any]
    machine_order_variables: Dict[MachineOrderKey, Any]
    flow_activation_variables: Dict[Tuple[SuboperationKey, SuboperationKey], Any]
    flow_variables: Dict[FlowArcKey, Any]
    makespan: Any
    horizon: int
    maximum_power: int
    variable_count: int
    constraint_count: int


def load_docplex_symbols() -> Dict[str, Any]:
    try:
        from docplex.mp.model import Model
        from docplex.mp.progress import ProgressClock, SolutionListener
    except ImportError as error:
        raise CplexMipUnavailableError(
            "the CPLEX MIP backend requires the 'docplex' and 'cplex' packages "
            "from IBM CPLEX Optimization Studio and a licensed local CPLEX "
            "runtime"
        ) from error
    return {
        "Model": Model,
        "ProgressClock": ProgressClock,
        "SolutionListener": SolutionListener,
    }


def expand_jssppr_entities(
    instance: Instance,
    domains: Domains,
) -> Tuple[Tuple[ExpandedEntity, ...], int]:
    if not instance.jobs or not instance.jobs[0]:
        raise ValueError("instance has no operations")
    if instance.horizon <= 0:
        raise ValueError("horizon must be positive")

    capacities = [int(value) for value in instance.power_thresholds]
    if any(value < 0 for value in capacities):
        raise ValueError("power thresholds must be non-negative")

    threshold_segments = compress_power_thresholds(capacities, instance.horizon)
    maximum_power = max(capacity for _, _, capacity in threshold_segments)

    entities: List[ExpandedEntity] = []
    operation_ids: List[int] = []
    for job_index, job in enumerate(instance.jobs):
        for job_position, operation in enumerate(job):
            entity_id = operation.id
            operation_ids.append(entity_id)
            if operation.duration <= 0:
                raise ValueError(
                    "operation {} must have a positive duration".format(entity_id)
                )
            if operation.nominal_power < 0 or operation.peak_power < 0:
                raise ValueError(
                    "operation {} has a negative power requirement".format(entity_id)
                )

            operation_lb = domains.earliest[entity_id]
            operation_ub = domains.latest[entity_id]
            if operation_lb > operation_ub:
                raise CplexMipHorizonInfeasibleError(
                    "horizon {} leaves no start time for operation {}".format(
                        instance.horizon, entity_id
                    )
                )

            peak_duration = effective_peak_duration(operation)
            remainder_duration = operation.duration - peak_duration
            phase_specs: List[Tuple[int, int]] = []
            if peak_duration > 0:
                phase_specs.append(
                    (peak_duration, operation.nominal_power + operation.peak_power)
                )
            if remainder_duration > 0:
                phase_specs.append((remainder_duration, operation.nominal_power))

            offset = 0
            phases: List[ExpandedSuboperation] = []
            for phase_index, (phase_duration, phase_power) in enumerate(phase_specs):
                phases.append(
                    ExpandedSuboperation(
                        key=(entity_id, phase_index),
                        entity_id=entity_id,
                        phase_index=phase_index,
                        offset=offset,
                        duration=phase_duration,
                        power=phase_power,
                        start_lb=operation_lb + offset,
                        start_ub=operation_ub + offset,
                        is_dummy=False,
                        real_operation_id=entity_id,
                    )
                )
                offset += phase_duration

            entities.append(
                ExpandedEntity(
                    entity_id=entity_id,
                    job_id=job_index,
                    machine_id=operation.machine,
                    job_position=job_position,
                    is_dummy=False,
                    real_operation_id=entity_id,
                    fixed_start=None,
                    suboperations=tuple(phases),
                )
            )

    next_entity_id = max(operation_ids, default=-1) + 1
    next_job_id = instance.n_jobs
    for segment_index, (segment_start, segment_end, capacity) in enumerate(
        threshold_segments
    ):
        duration = segment_end - segment_start
        if duration <= 0:
            continue
        entity_id = next_entity_id + segment_index
        dummy_phase = ExpandedSuboperation(
            key=(entity_id, 0),
            entity_id=entity_id,
            phase_index=0,
            offset=0,
            duration=duration,
            power=maximum_power - int(capacity),
            start_lb=segment_start,
            start_ub=segment_start,
            is_dummy=True,
            real_operation_id=None,
        )
        entities.append(
            ExpandedEntity(
                entity_id=entity_id,
                job_id=next_job_id + segment_index,
                machine_id=None,
                job_position=None,
                is_dummy=True,
                real_operation_id=None,
                fixed_start=segment_start,
                suboperations=(dummy_phase,),
            )
        )

    return tuple(entities), maximum_power


def _same_job_forward_arc(
    source_entity: ExpandedEntity,
    source: ExpandedSuboperation,
    target_entity: ExpandedEntity,
    target: ExpandedSuboperation,
) -> bool:
    if source_entity.entity_id == target_entity.entity_id:
        return source.phase_index < target.phase_index
    if source_entity.is_dummy or target_entity.is_dummy:
        return False
    return int(source_entity.job_position) < int(target_entity.job_position)


def _can_precede(
    source: ExpandedSuboperation,
    target: ExpandedSuboperation,
) -> bool:
    return source.earliest_end <= target.start_ub


def _tight_time_big_m(
    source: ExpandedSuboperation,
    target: ExpandedSuboperation,
) -> int:
    return max(0, source.latest_end - target.start_lb)


def eligible_power_arcs(
    entities: Sequence[ExpandedEntity],
) -> Tuple[Tuple[SuboperationKey, SuboperationKey], ...]:
    entity_by_id = {entity.entity_id: entity for entity in entities}
    suboperations = [
        suboperation
        for entity in entities
        for suboperation in entity.suboperations
        if suboperation.power > 0
    ]
    arcs: List[Tuple[SuboperationKey, SuboperationKey]] = []
    for source in suboperations:
        source_entity = entity_by_id[source.entity_id]
        for target in suboperations:
            if source.key == target.key:
                continue
            target_entity = entity_by_id[target.entity_id]
            if source_entity.job_id == target_entity.job_id:
                if not _same_job_forward_arc(
                    source_entity, source, target_entity, target
                ):
                    continue
            elif not _can_precede(source, target):
                continue
            arcs.append((source.key, target.key))
    return tuple(arcs)


def safe_end(model: Any) -> None:
    try:
        model.end()
    except Exception:
        pass


def _add_constraint(model: Any, expression: Any, name: str) -> int:
    model.add_constraint(expression, ctname=name)
    return 1


def _machine_pairs(
    entities: Sequence[ExpandedEntity],
) -> Iterable[Tuple[ExpandedEntity, ExpandedEntity]]:
    by_machine: Dict[int, List[ExpandedEntity]] = {}
    for entity in entities:
        if not entity.is_dummy:
            by_machine.setdefault(int(entity.machine_id), []).append(entity)
    for machine_entities in by_machine.values():
        for left, right in combinations(machine_entities, 2):
            if left.job_id != right.job_id:
                yield left, right


def _normalize_warm_start(
    entities: Sequence[ExpandedEntity],
    instance: Instance,
    warm_start: Optional[Sequence[Sequence[int]]],
) -> Optional[Dict[int, int]]:
    if warm_start is None:
        return None

    flat: Dict[int, int] = {}
    for job_index, job in enumerate(instance.jobs):
        for operation_index, operation in enumerate(job):
            try:
                flat[operation.id] = int(warm_start[job_index][operation_index])
            except (IndexError, TypeError, ValueError):
                return None

    starts: Dict[int, int] = {}
    for entity in entities:
        if entity.is_dummy:
            continue
        start = flat.get(entity.entity_id)
        if start is None:
            return None
        if (
            start < entity.first.start_lb
            or start > entity.first.start_ub
            or start + entity.duration > instance.horizon
        ):
            return None
        starts[entity.entity_id] = start
    return starts


def _install_partial_mip_start(
    model: Any,
    entities: Sequence[ExpandedEntity],
    instance: Instance,
    suboperation_starts: Mapping[SuboperationKey, Any],
    machine_order_variables: Mapping[MachineOrderKey, Any],
    warm_start: Optional[Sequence[Sequence[int]]],
) -> bool:
    normalized = _normalize_warm_start(entities, instance, warm_start)
    if normalized is None:
        return False

    solution = model.new_solution()
    entity_by_id = {entity.entity_id: entity for entity in entities}
    for entity in entities:
        if entity.is_dummy:
            continue
        operation_start = normalized[entity.entity_id]
        for suboperation in entity.suboperations:
            solution.add_var_value(
                suboperation_starts[suboperation.key],
                operation_start + suboperation.offset,
            )

    for (source_id, target_id), variable in machine_order_variables.items():
        source = entity_by_id[source_id]
        solution.add_var_value(
            variable,
            1
            if normalized[source_id] + source.duration <= normalized[target_id]
            else 0,
        )

    return model.add_mip_start(solution, complete_vars=False) is not None


def build_cplex_mip_model(
    instance: Instance,
    domains: Domains,
    warm_start: Optional[Sequence[Sequence[int]]] = None,
    model_name: str = "JSSPPR_CPLEX_MIP",
) -> CplexMipModel:
    symbols = load_docplex_symbols()
    entities, maximum_power = expand_jssppr_entities(instance, domains)
    entity_by_id = {entity.entity_id: entity for entity in entities}
    suboperations = {
        suboperation.key: suboperation
        for entity in entities
        for suboperation in entity.suboperations
    }
    real_entities = [entity for entity in entities if not entity.is_dummy]
    horizon = instance.horizon

    model = symbols["Model"](name=model_name)
    try:
        suboperation_starts: Dict[SuboperationKey, Any] = {}
        for suboperation in suboperations.values():
            if suboperation.is_dummy:
                lower_bound = 0
                upper_bound = horizon - suboperation.duration
            else:
                lower_bound = suboperation.start_lb
                upper_bound = suboperation.start_ub
            suboperation_starts[suboperation.key] = model.integer_var(
                lb=lower_bound,
                ub=upper_bound,
                name="s_e{}_p{}".format(
                    suboperation.entity_id, suboperation.phase_index
                ),
            )

        start_variables = {
            int(entity.real_operation_id): suboperation_starts[entity.first.key]
            for entity in real_entities
        }
        makespan = model.integer_var(lb=0, ub=horizon, name="cmax")
        machine_order_variables: Dict[MachineOrderKey, Any] = {}
        flow_activation_variables: Dict[
            Tuple[SuboperationKey, SuboperationKey], Any
        ] = {}
        flow_variables: Dict[FlowArcKey, Any] = {}
        constraint_count = 0

        model.minimize(makespan)

        for entity in real_entities:
            last = entity.last
            constraint_count += _add_constraint(
                model,
                suboperation_starts[last.key] + last.duration <= makespan,
                "eq02_cmax_e{}".format(entity.entity_id),
            )

        for left, right in _machine_pairs(real_entities):
            directions = ((left, right), (right, left))
            feasible_directions = [
                (source, target)
                for source, target in directions
                if _can_precede(source.last, target.first)
            ]
            if not feasible_directions:
                raise CplexMipHorizonInfeasibleError(
                    "horizon {} leaves no feasible machine order between "
                    "operations {} and {}".format(
                        horizon, left.entity_id, right.entity_id
                    )
                )

            if len(feasible_directions) == 1:
                source, target = feasible_directions[0]
                constraint_count += _add_constraint(
                    model,
                    suboperation_starts[target.first.key]
                    >= suboperation_starts[source.last.key] + source.last.duration,
                    "eq06_fixed_e{}_before_e{}".format(
                        source.entity_id, target.entity_id
                    ),
                )
                continue

            pair_variables: List[Any] = []
            for source, target in feasible_directions:
                key = (source.entity_id, target.entity_id)
                order_variable = model.binary_var(name="x_e{}_e{}".format(*key))
                machine_order_variables[key] = order_variable
                pair_variables.append(order_variable)
                big_m = _tight_time_big_m(source.last, target.first)
                constraint_count += _add_constraint(
                    model,
                    suboperation_starts[target.first.key]
                    >= suboperation_starts[source.last.key]
                    + source.last.duration
                    - big_m * (1 - order_variable),
                    "eq06_e{}_before_e{}".format(*key),
                )
            constraint_count += _add_constraint(
                model,
                model.sum(pair_variables) == 1,
                "eq03_machine_pair_e{}_e{}".format(left.entity_id, right.entity_id),
            )

        real_by_job: Dict[int, List[ExpandedEntity]] = {}
        for entity in real_entities:
            real_by_job.setdefault(entity.job_id, []).append(entity)
        for job_id, job_entities in real_by_job.items():
            job_entities.sort(key=lambda entity: int(entity.job_position))
            for earlier_index, earlier in enumerate(job_entities):
                for later in job_entities[earlier_index + 1 :]:
                    constraint_count += _add_constraint(
                        model,
                        suboperation_starts[later.first.key]
                        >= suboperation_starts[earlier.last.key]
                        + earlier.last.duration,
                        "eq04_job{}_e{}_e{}".format(
                            job_id, earlier.entity_id, later.entity_id
                        ),
                    )

        for entity in real_entities:
            for predecessor, successor in zip(
                entity.suboperations, entity.suboperations[1:]
            ):
                constraint_count += _add_constraint(
                    model,
                    suboperation_starts[successor.key]
                    == suboperation_starts[predecessor.key] + predecessor.duration,
                    "eq05_nowait_e{}_p{}".format(
                        entity.entity_id, successor.phase_index
                    ),
                )

        positive_suboperations = [
            suboperation
            for suboperation in suboperations.values()
            if suboperation.power > 0
        ]

        for target in positive_suboperations:
            upper_bound = min(maximum_power, target.power)
            if upper_bound <= 0:
                continue
            flow_variables[(None, target.key)] = model.continuous_var(
                lb=0,
                ub=upper_bound,
                name="phi_source_e{}_p{}".format(
                    target.entity_id, target.phase_index
                ),
            )

        for source_key, target_key in eligible_power_arcs(entities):
            source = suboperations[source_key]
            target = suboperations[target_key]
            upper_bound = min(maximum_power, source.power, target.power)
            if upper_bound <= 0:
                continue
            flow_variables[(source_key, target_key)] = model.continuous_var(
                lb=0,
                ub=upper_bound,
                name="phi_e{}_p{}_e{}_p{}".format(
                    source.entity_id,
                    source.phase_index,
                    target.entity_id,
                    target.phase_index,
                ),
            )
            flow_activation_variables[(source_key, target_key)] = model.binary_var(
                name="y_e{}_p{}_e{}_p{}".format(
                    source.entity_id,
                    source.phase_index,
                    target.entity_id,
                    target.phase_index,
                )
            )

        source_flows = [
            variable
            for (source_key, _), variable in flow_variables.items()
            if source_key is None
        ]
        if source_flows:
            constraint_count += _add_constraint(
                model,
                model.sum(source_flows) <= maximum_power,
                "eq07_source_capacity",
            )

        incoming: Dict[SuboperationKey, List[Any]] = {
            suboperation.key: [] for suboperation in positive_suboperations
        }
        outgoing: Dict[SuboperationKey, List[Any]] = {
            suboperation.key: [] for suboperation in positive_suboperations
        }
        for (source_key, target_key), variable in flow_variables.items():
            incoming[target_key].append(variable)
            if source_key is not None:
                outgoing[source_key].append(variable)

        for target in positive_suboperations:
            target_incoming = incoming[target.key]
            if not target_incoming:
                constraint_count += _add_constraint(
                    model,
                    makespan <= -1,
                    "eq08_infeasible_e{}_p{}".format(
                        target.entity_id, target.phase_index
                    ),
                )
            else:
                constraint_count += _add_constraint(
                    model,
                    model.sum(target_incoming) == target.power,
                    "eq08_balance_e{}_p{}".format(
                        target.entity_id, target.phase_index
                    ),
                )

        for source in positive_suboperations:
            source_outgoing = outgoing[source.key]
            if source_outgoing:
                constraint_count += _add_constraint(
                    model,
                    model.sum(source_outgoing) <= source.power,
                    "eq09_release_e{}_p{}".format(
                        source.entity_id, source.phase_index
                    ),
                )

        for entity in entities:
            if entity.is_dummy:
                constraint_count += _add_constraint(
                    model,
                    suboperation_starts[entity.first.key] == int(entity.fixed_start),
                    "eq10_dummy_e{}".format(entity.entity_id),
                )

        for arc, activation in flow_activation_variables.items():
            flow = flow_variables[arc]
            constraint_count += _add_constraint(
                model,
                flow <= float(flow.ub) * activation,
                "eq11_flow_implies_y_e{}_p{}_e{}_p{}".format(
                    arc[0][0], arc[0][1], arc[1][0], arc[1][1]
                ),
            )
            constraint_count += _add_constraint(
                model,
                activation <= flow,
                "eq12_y_implies_flow_e{}_p{}_e{}_p{}".format(
                    arc[0][0], arc[0][1], arc[1][0], arc[1][1]
                ),
            )

        for (source_key, target_key), activation in flow_activation_variables.items():
            source = suboperations[source_key]
            target = suboperations[target_key]
            if (
                entity_by_id[source.entity_id].job_id
                == entity_by_id[target.entity_id].job_id
            ):
                continue
            big_m = _tight_time_big_m(source, target)
            constraint_count += _add_constraint(
                model,
                suboperation_starts[target.key]
                >= suboperation_starts[source.key]
                + source.duration
                - big_m * (1 - activation),
                "eq13_time_e{}_p{}_e{}_p{}".format(
                    source.entity_id,
                    source.phase_index,
                    target.entity_id,
                    target.phase_index,
                ),
            )

        _install_partial_mip_start(
            model,
            entities,
            instance,
            suboperation_starts,
            machine_order_variables,
            warm_start,
        )

        return CplexMipModel(
            model=model,
            entities=entities,
            suboperations=suboperations,
            start_variables=start_variables,
            suboperation_starts=suboperation_starts,
            machine_order_variables=machine_order_variables,
            flow_activation_variables=flow_activation_variables,
            flow_variables=flow_variables,
            makespan=makespan,
            horizon=horizon,
            maximum_power=maximum_power,
            variable_count=int(model.number_of_variables),
            constraint_count=int(model.number_of_constraints),
        )
    except BaseException:
        safe_end(model)
        raise


def _solution_value(solution: Any, variable: Any) -> Any:
    getter = getattr(solution, "get_value", None)
    if callable(getter):
        return getter(variable)
    return solution[variable]


def _tolerance_safe_integer(value: Any, label: str, tolerance: float) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "CPLEX returned a non-numeric value for {}: {!r}".format(label, value)
        ) from error
    if not isfinite(numeric):
        raise RuntimeError(
            "CPLEX returned a non-finite value for {}: {!r}".format(label, value)
        )
    rounded = int(round(numeric))
    if abs(numeric - rounded) > tolerance:
        raise RuntimeError(
            "CPLEX integer value for {} is outside tolerance: {}".format(
                label, numeric
            )
        )
    return rounded


def extract_start_matrix(
    instance: Instance,
    start_variables: Mapping[int, Any],
    solution: Any,
    tolerance: float = 1e-5,
) -> List[List[int]]:
    if solution is None:
        raise RuntimeError("CPLEX returned no incumbent solution")

    starts: List[List[int]] = []
    for job in instance.jobs:
        row: List[int] = []
        for operation in job:
            try:
                variable = start_variables[operation.id]
            except KeyError as error:
                raise RuntimeError(
                    "MIP model is missing the start variable for operation "
                    "{}".format(operation.id)
                ) from error
            start = _tolerance_safe_integer(
                _solution_value(solution, variable),
                "operation {}".format(operation.id),
                tolerance,
            )
            if start < 0:
                raise RuntimeError(
                    "CPLEX returned a negative start for operation {}".format(
                        operation.id
                    )
                )
            row.append(start)
        starts.append(row)
    return starts
