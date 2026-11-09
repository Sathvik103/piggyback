"""Choose one recovery option per shipment under shared leg capacity (step 4).

    minimise   sum(penalty[o] * x[o])
    subject to sum(x[o] for o of shipment s) == 1              for every shipment
               sum(kg[o] * x[o] for o using leg l) <= spare_kg  for every leg
               sum(m3[o] * x[o] for o using leg l) <= spare_m3  for every leg

`solve_cpsat` solves it exactly with OR-Tools CP-SAT (integers: kg x10, m3 x1000,
penalty in whole rupees). `solve_greedy` and `solve_none` are the baselines.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from detection.view import OperatorView

from .options import MODE_NONE, RecoveryOption

KG_SCALE = 10
M3_SCALE = 1000


@dataclass
class Plan:
    solver: str
    chosen: dict[str, RecoveryOption]
    objective_inr: float
    status: str
    solve_s: float
    stats: dict = field(default_factory=dict)


def leg_capacity(view: OperatorView) -> dict[str, tuple[float, float]]:
    return {l.leg_id: (l.spare_kg, l.spare_m3) for l in view.legs}


def _plan(solver, chosen, status, t0, **stats) -> Plan:
    return Plan(solver, chosen, round(sum(o.penalty_inr for o in chosen.values()), 2), status,
                round(time.perf_counter() - t0, 3), stats)


def solve_none(options: dict[str, list[RecoveryOption]], capacity=None) -> Plan:
    t0 = time.perf_counter()
    return _plan("none", {sid: next(o for o in opts if o.mode == MODE_NONE)
                          for sid, opts in options.items()}, "BASELINE", t0)


def solve_greedy(options: dict[str, list[RecoveryOption]],
                 capacity: dict[str, tuple[float, float]]) -> Plan:
    """Shipments with most to lose go first; each takes its cheapest option that still fits."""
    t0 = time.perf_counter()
    left = dict(capacity)
    chosen: dict[str, RecoveryOption] = {}
    order = sorted(options, key=lambda sid: -max(o.penalty_inr for o in options[sid]))
    for sid in order:
        for o in sorted(options[sid], key=lambda o: o.penalty_inr):
            if all(left[l][0] >= o.weight_kg and left[l][1] >= o.volume_m3 for l in o.capacity_legs):
                for l in o.capacity_legs:
                    left[l] = (left[l][0] - o.weight_kg, left[l][1] - o.volume_m3)
                chosen[sid] = o
                break
    return _plan("greedy", chosen, "HEURISTIC", t0)


def solve_cpsat(options: dict[str, list[RecoveryOption]],
                capacity: dict[str, tuple[float, float]],
                time_limit_s: float = 10.0, workers: int = 8, seed: int = 0) -> Plan:
    t0 = time.perf_counter()
    m = cp_model.CpModel()
    x: dict[str, cp_model.IntVar] = {}
    users: dict[str, list[RecoveryOption]] = {}
    for opts in options.values():
        for o in opts:
            x[o.option_id] = m.new_bool_var(o.option_id)
            for l in o.capacity_legs:
                users.setdefault(l, []).append(o)
        m.add_exactly_one(x[o.option_id] for o in opts)

    for l, os_ in users.items():
        kg, m3 = capacity[l]
        # Only constrain legs that could actually be overloaded.
        if sum(o.weight_kg for o in os_) > kg:
            m.add(sum(math.ceil(o.weight_kg * KG_SCALE) * x[o.option_id] for o in os_)
                  <= math.floor(kg * KG_SCALE))
        if sum(o.volume_m3 for o in os_) > m3:
            m.add(sum(math.ceil(o.volume_m3 * M3_SCALE) * x[o.option_id] for o in os_)
                  <= math.floor(m3 * M3_SCALE))

    m.minimize(sum(round(o.penalty_inr) * x[o.option_id]
                   for opts in options.values() for o in opts))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    status = solver.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"CP-SAT returned {solver.status_name(status)}")
    chosen = {sid: next(o for o in opts if solver.value(x[o.option_id]))
              for sid, opts in options.items()}
    return _plan("cpsat", chosen, solver.status_name(status), t0,
                 bound_inr=round(solver.best_objective_bound, 2),
                 contested_legs=sum(1 for os_ in users.values()
                                    if len({o.shipment_id for o in os_}) > 1))


def capacity_violations(plan: Plan, capacity: dict[str, tuple[float, float]]) -> list[str]:
    load: dict[str, list[float]] = {}
    for o in plan.chosen.values():
        for l in o.capacity_legs:
            kg_m3 = load.setdefault(l, [0.0, 0.0])
            kg_m3[0] += o.weight_kg
            kg_m3[1] += o.volume_m3
    return [l for l, (kg, m3) in load.items()
            if kg > capacity[l][0] + 1e-6 or m3 > capacity[l][1] + 1e-6]


SOLVERS = {"cpsat": solve_cpsat, "greedy": solve_greedy, "none": solve_none}
