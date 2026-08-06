from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .cplex_mip import (
    ExpandedEntity,
    MachineOrderKey,
    SuboperationKey,
    _can_precede,
    _machine_pairs,
    _tight_time_big_m,
    eligible_power_arcs,
    expand_jssppr_entities,
)
from .model import Domains, Instance


FlowArcKey = Tuple[Optional[SuboperationKey], SuboperationKey]


class GurobiUnavailableError(RuntimeError):
    pass


class GurobiHorizonInfeasibleError(ValueError):
    pass


@dataclass
class GurobiMipModel:
    model: Any
    entities: Tuple[ExpandedEntity, ...]
    suboperations: Dict[SuboperationKey, Any]
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


def load_gurobi_symbols() -> Dict[str, Any]:
    try:
        import gurobipy
        from gurobipy import GRB
    except ImportError as error:
        raise GurobiUnavailableError(
            "the Gurobi backend requires the 'gurobipy' package and a Gurobi "
            "license; install it with `pip install gurobipy` and activate a "
            "license large enough for the model"
        ) from error
    return {"gurobipy": gurobipy, "GRB": GRB}


def safe_dispose(model: Any) -> None:
    try:
        model.dispose()
    except Exception:
        pass


def translate_license_error(error: BaseException) -> BaseException:
    if "size-limited" in str(error).lower():
        return GurobiUnavailableError(
            "the Gurobi model for this instance exceeds the size-limited "
            "license bundled with the gurobipy wheel; install a full Gurobi "
            "license before running this backend: {}".format(error)
        )
    return error


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


def _install_mip_start(
    entities: Sequence[ExpandedEntity],
    instance: Instance,
    suboperation_starts: Mapping[SuboperationKey, Any],
    machine_order_variables: Mapping[MachineOrderKey, Any],
    warm_start: Optional[Sequence[Sequence[int]]],
) -> bool:
    normalized = _normalize_warm_start(entities, instance, warm_start)
    if normalized is None:
        return False

    entity_by_id = {entity.entity_id: entity for entity in entities}
    for entity in entities:
        if entity.is_dummy:
            continue
        operation_start = normalized[entity.entity_id]
        for suboperation in entity.suboperations:
            suboperation_starts[suboperation.key].Start = (
                operation_start + suboperation.offset
            )

    for (source_id, target_id), variable in machine_order_variables.items():
        source = entity_by_id[source_id]
        variable.Start = (
            1
            if normalized[source_id] + source.duration <= normalized[target_id]
            else 0
        )
    return True


def build_gurobi_mip_model(
    instance: Instance,
    domains: Domains,
    warm_start: Optional[Sequence[Sequence[int]]] = None,
    model_name: str = "JSSPPR_GUROBI_MIP",
) -> GurobiMipModel:
    symbols = load_gurobi_symbols()
    gurobipy = symbols["gurobipy"]
    GRB = symbols["GRB"]

    entities, maximum_power = expand_jssppr_entities(instance, domains)
    entity_by_id = {entity.entity_id: entity for entity in entities}
    suboperations = {
        suboperation.key: suboperation
        for entity in entities
        for suboperation in entity.suboperations
    }
    real_entities = [entity for entity in entities if not entity.is_dummy]
    horizon = instance.horizon

    try:
        model = gurobipy.Model(model_name)
    except Exception as error:
        raise GurobiUnavailableError(
            "unable to create a Gurobi model; confirm that a valid Gurobi "
            "license is installed and visible to this machine: {}".format(error)
        ) from error

    try:
        model.Params.OutputFlag = 0

        suboperation_starts: Dict[SuboperationKey, Any] = {}
        for suboperation in suboperations.values():
            if suboperation.is_dummy:
                lower_bound = 0
                upper_bound = horizon - suboperation.duration
            else:
                lower_bound = suboperation.start_lb
                upper_bound = suboperation.start_ub
            suboperation_starts[suboperation.key] = model.addVar(
                lb=lower_bound,
                ub=upper_bound,
                vtype=GRB.INTEGER,
                name="s_e{}_p{}".format(
                    suboperation.entity_id, suboperation.phase_index
                ),
            )

        start_variables = {
            int(entity.real_operation_id): suboperation_starts[entity.first.key]
            for entity in real_entities
        }
        makespan = model.addVar(
            lb=0, ub=horizon, vtype=GRB.INTEGER, name="cmax"
        )
        machine_order_variables: Dict[MachineOrderKey, Any] = {}
        flow_activation_variables: Dict[
            Tuple[SuboperationKey, SuboperationKey], Any
        ] = {}
        flow_variables: Dict[FlowArcKey, Any] = {}
        flow_bounds: Dict[FlowArcKey, float] = {}
        constraint_count = 0

        model.setObjective(makespan, GRB.MINIMIZE)

        for entity in real_entities:
            last = entity.last
            model.addConstr(
                suboperation_starts[last.key] + last.duration <= makespan,
                name="eq02_cmax_e{}".format(entity.entity_id),
            )
            constraint_count += 1

        for left, right in _machine_pairs(real_entities):
            directions = ((left, right), (right, left))
            feasible_directions = [
                (source, target)
                for source, target in directions
                if _can_precede(source.last, target.first)
            ]
            if not feasible_directions:
                raise GurobiHorizonInfeasibleError(
                    "horizon {} leaves no feasible machine order between "
                    "operations {} and {}".format(
                        horizon, left.entity_id, right.entity_id
                    )
                )

            if len(feasible_directions) == 1:
                source, target = feasible_directions[0]
                model.addConstr(
                    suboperation_starts[target.first.key]
                    >= suboperation_starts[source.last.key] + source.last.duration,
                    name="eq06_fixed_e{}_before_e{}".format(
                        source.entity_id, target.entity_id
                    ),
                )
                constraint_count += 1
                continue

            pair_variables: List[Any] = []
            for source, target in feasible_directions:
                key = (source.entity_id, target.entity_id)
                order_variable = model.addVar(
                    vtype=GRB.BINARY, name="x_e{}_e{}".format(*key)
                )
                machine_order_variables[key] = order_variable
                pair_variables.append(order_variable)
                big_m = _tight_time_big_m(source.last, target.first)
                model.addConstr(
                    suboperation_starts[target.first.key]
                    >= suboperation_starts[source.last.key]
                    + source.last.duration
                    - big_m * (1 - order_variable),
                    name="eq06_e{}_before_e{}".format(*key),
                )
                constraint_count += 1
            model.addConstr(
                gurobipy.quicksum(pair_variables) == 1,
                name="eq03_machine_pair_e{}_e{}".format(
                    left.entity_id, right.entity_id
                ),
            )
            constraint_count += 1

        real_by_job: Dict[int, List[ExpandedEntity]] = {}
        for entity in real_entities:
            real_by_job.setdefault(entity.job_id, []).append(entity)
        for job_id, job_entities in real_by_job.items():
            job_entities.sort(key=lambda entity: int(entity.job_position))
            for earlier_index, earlier in enumerate(job_entities):
                for later in job_entities[earlier_index + 1 :]:
                    model.addConstr(
                        suboperation_starts[later.first.key]
                        >= suboperation_starts[earlier.last.key]
                        + earlier.last.duration,
                        name="eq04_job{}_e{}_e{}".format(
                            job_id, earlier.entity_id, later.entity_id
                        ),
                    )
                    constraint_count += 1

        for entity in real_entities:
            for predecessor, successor in zip(
                entity.suboperations, entity.suboperations[1:]
            ):
                model.addConstr(
                    suboperation_starts[successor.key]
                    == suboperation_starts[predecessor.key] + predecessor.duration,
                    name="eq05_nowait_e{}_p{}".format(
                        entity.entity_id, successor.phase_index
                    ),
                )
                constraint_count += 1

        positive_suboperations = [
            suboperation
            for suboperation in suboperations.values()
            if suboperation.power > 0
        ]

        for target in positive_suboperations:
            upper_bound = min(maximum_power, target.power)
            if upper_bound <= 0:
                continue
            key = (None, target.key)
            flow_variables[key] = model.addVar(
                lb=0,
                ub=upper_bound,
                vtype=GRB.CONTINUOUS,
                name="phi_source_e{}_p{}".format(
                    target.entity_id, target.phase_index
                ),
            )
            flow_bounds[key] = float(upper_bound)

        for source_key, target_key in eligible_power_arcs(entities):
            source = suboperations[source_key]
            target = suboperations[target_key]
            upper_bound = min(maximum_power, source.power, target.power)
            if upper_bound <= 0:
                continue
            key = (source_key, target_key)
            flow_variables[key] = model.addVar(
                lb=0,
                ub=upper_bound,
                vtype=GRB.CONTINUOUS,
                name="phi_e{}_p{}_e{}_p{}".format(
                    source.entity_id,
                    source.phase_index,
                    target.entity_id,
                    target.phase_index,
                ),
            )
            flow_bounds[key] = float(upper_bound)
            flow_activation_variables[(source_key, target_key)] = model.addVar(
                vtype=GRB.BINARY,
                name="y_e{}_p{}_e{}_p{}".format(
                    source.entity_id,
                    source.phase_index,
                    target.entity_id,
                    target.phase_index,
                ),
            )

        source_flows = [
            variable
            for (source_key, _), variable in flow_variables.items()
            if source_key is None
        ]
        if source_flows:
            model.addConstr(
                gurobipy.quicksum(source_flows) <= maximum_power,
                name="eq07_source_capacity",
            )
            constraint_count += 1

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
                model.addConstr(
                    makespan <= -1,
                    name="eq08_infeasible_e{}_p{}".format(
                        target.entity_id, target.phase_index
                    ),
                )
            else:
                model.addConstr(
                    gurobipy.quicksum(target_incoming) == target.power,
                    name="eq08_balance_e{}_p{}".format(
                        target.entity_id, target.phase_index
                    ),
                )
            constraint_count += 1

        for source in positive_suboperations:
            source_outgoing = outgoing[source.key]
            if source_outgoing:
                model.addConstr(
                    gurobipy.quicksum(source_outgoing) <= source.power,
                    name="eq09_release_e{}_p{}".format(
                        source.entity_id, source.phase_index
                    ),
                )
                constraint_count += 1

        for entity in entities:
            if entity.is_dummy:
                model.addConstr(
                    suboperation_starts[entity.first.key] == int(entity.fixed_start),
                    name="eq10_dummy_e{}".format(entity.entity_id),
                )
                constraint_count += 1

        for arc, activation in flow_activation_variables.items():
            flow = flow_variables[arc]
            model.addConstr(
                flow <= flow_bounds[arc] * activation,
                name="eq11_flow_implies_y_e{}_p{}_e{}_p{}".format(
                    arc[0][0], arc[0][1], arc[1][0], arc[1][1]
                ),
            )
            model.addConstr(
                activation <= flow,
                name="eq12_y_implies_flow_e{}_p{}_e{}_p{}".format(
                    arc[0][0], arc[0][1], arc[1][0], arc[1][1]
                ),
            )
            constraint_count += 2

        for (source_key, target_key), activation in flow_activation_variables.items():
            source = suboperations[source_key]
            target = suboperations[target_key]
            if (
                entity_by_id[source.entity_id].job_id
                == entity_by_id[target.entity_id].job_id
            ):
                continue
            big_m = _tight_time_big_m(source, target)
            model.addConstr(
                suboperation_starts[target.key]
                >= suboperation_starts[source.key]
                + source.duration
                - big_m * (1 - activation),
                name="eq13_time_e{}_p{}_e{}_p{}".format(
                    source.entity_id,
                    source.phase_index,
                    target.entity_id,
                    target.phase_index,
                ),
            )
            constraint_count += 1

        model.update()

        _install_mip_start(
            entities,
            instance,
            suboperation_starts,
            machine_order_variables,
            warm_start,
        )

        return GurobiMipModel(
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
            variable_count=int(model.NumVars),
            constraint_count=int(model.NumConstrs),
        )
    except Exception as error:
        safe_dispose(model)
        translated = translate_license_error(error)
        if translated is not error:
            raise translated from error
        raise
    except BaseException:
        safe_dispose(model)
        raise


def _tolerance_safe_integer(value: Any, label: str, tolerance: float) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(
            "Gurobi returned a non-numeric value for {}: {!r}".format(label, value)
        ) from error
    if not isfinite(numeric):
        raise RuntimeError(
            "Gurobi returned a non-finite value for {}: {!r}".format(label, value)
        )
    rounded = int(round(numeric))
    if abs(numeric - rounded) > tolerance:
        raise RuntimeError(
            "Gurobi integer value for {} is outside tolerance: {}".format(
                label, numeric
            )
        )
    return rounded


def extract_start_matrix(
    instance: Instance,
    start_variables: Mapping[int, Any],
    tolerance: float = 1e-5,
) -> List[List[int]]:
    starts: List[List[int]] = []
    for job in instance.jobs:
        row: List[int] = []
        for operation in job:
            try:
                variable = start_variables[operation.id]
            except KeyError as error:
                raise RuntimeError(
                    "Gurobi model is missing the start variable for operation "
                    "{}".format(operation.id)
                ) from error
            start = _tolerance_safe_integer(
                variable.X,
                "operation {}".format(operation.id),
                tolerance,
            )
            if start < 0:
                raise RuntimeError(
                    "Gurobi returned a negative start for operation {}".format(
                        operation.id
                    )
                )
            row.append(start)
        starts.append(row)
    return starts
