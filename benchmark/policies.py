"""Recovery policies compared by the benchmark.

Every policy gets the same detections (grace 2 h) and returns a Plan; the replay
then scores it against the truth. That isolates *recovery* from detection.

Baselines model what carriers commonly do today:

- do_nothing: wait for the next manual review.
- next_service: a TMS-style auto-rebook. From where the parcel is, book the
  earliest service with room on the *published* timetable (it doesn't know a
  trip is running late or that a disruption was announced), one parcel at a
  time in the order problems were seen (first come, first served). It acts only
  on parcels already stranded; a parcel that strands later is rebooked when that
  shows up in scans (see `reactive_followup`).
- next_service_express_truck: the same, plus the control-tower rule of thumb
  "send a dedicated truck if an EXPRESS parcel would be late".

Piggyback and its ablations each change one ingredient:

- piggyback: delay- and disruption-aware network, CP-SAT over all stranded
  parcels and options jointly, and it acts once a parcel is stranded (AT_RISK
  parcels are watched, then rebooked if they really strand).
- piggyback_greedy: same options, greedy assignment instead of the optimizer.
- piggyback_proactive: also rebooks AT_RISK parcels right away. This was the
  original default; the benchmark showed it doesn't pay, so it became an
  ablation (see docs/DECISIONS.md).
- piggyback_blind: plans on the published timetable, like the baselines.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Callable

from detection.detector import KIND_AT_RISK, KIND_MISSED_CONNECTION, Detection
from detection.view import OperatorView
from recovery import Network, OptionsConfig, Plan, RecoveryOption, generate_options, leg_capacity
from recovery.optimizer import solve_cpsat, solve_greedy
from recovery.options import MODE_DEDICATED, MODE_LOCAL, MODE_NONE, MODE_PIGGYBACK
from simulator.generator import actual_scans

CPSAT_WORKERS = 2          # several scenarios run in parallel; don't oversubscribe
CPSAT_TIME_LIMIT_S = 10.0


@dataclass(frozen=True)
class Policy:
    name: str
    family: str            # "baseline" | "piggyback" | "ablation"
    label: str
    run: Callable[[OperatorView, list[Detection]], Plan]
    # Reactive policies don't act on AT_RISK parcels now, but they would act once
    # such a parcel really strands. "next_service" or "best" says how; None = never.
    followup: str | None = None


def _reactive(dets: list[Detection]) -> list[Detection]:
    return [d for d in dets if d.kind != KIND_AT_RISK]


def _blind(view: OperatorView) -> Network:
    return Network(view, trip_delays={}, disruptions=False)


def _plan(name: str, chosen: dict[str, RecoveryOption], t0: float, status: str = "HEURISTIC") -> Plan:
    return Plan(name, chosen, round(sum(o.penalty_inr for o in chosen.values()), 2), status,
                round(time.perf_counter() - t0, 3))


def do_nothing(view: OperatorView, dets: list[Detection]) -> Plan:
    return _plan("do_nothing", {}, time.perf_counter(), "BASELINE")


def next_service(view: OperatorView, dets: list[Detection], express_truck: bool = False) -> Plan:
    t0 = time.perf_counter()
    net = _blind(view)
    cfg = OptionsConfig(k_piggyback=1)
    left = leg_capacity(view)
    ships = {s.shipment_id: s for s in view.shipments}
    chosen: dict[str, RecoveryOption] = {}
    for d in sorted(_reactive(dets), key=lambda d: (d.last_scan_h, d.shipment_id)):
        opts = generate_options(view, [d], cfg, network=net, capacity=left)[d.shipment_id]
        first = {}
        for o in opts:
            first.setdefault(o.mode, o)
        pick = first.get(MODE_PIGGYBACK) or first.get(MODE_LOCAL)
        if (express_truck and ships[d.shipment_id].priority == "EXPRESS" and MODE_DEDICATED in first
                and (pick is None or pick.lateness_h > 0)):
            pick = first[MODE_DEDICATED]
        pick = pick or first[MODE_NONE]
        for leg in pick.capacity_legs:
            kg, m3 = left[leg]
            left[leg] = (kg - pick.weight_kg, m3 - pick.volume_m3)
        chosen[d.shipment_id] = pick
    return _plan("next_service_express_truck" if express_truck else "next_service", chosen, t0)


def piggyback(view: OperatorView, dets: list[Detection], aware: bool = True, proactive: bool = False,
              optimizer: str = "cpsat") -> Plan:
    t0 = time.perf_counter()
    net = Network(view) if aware else _blind(view)
    opts = generate_options(view, dets if proactive else _reactive(dets), network=net)
    cap = leg_capacity(view)
    if optimizer == "cpsat":
        plan = solve_cpsat(opts, cap, time_limit_s=CPSAT_TIME_LIMIT_S, workers=CPSAT_WORKERS)
    else:
        plan = solve_greedy(opts, cap)
    plan.solve_s = round(time.perf_counter() - t0, 3)  # include option generation
    return plan


def reactive_followup(view: OperatorView, dets: list[Detection], plan: Plan, true_delays: dict[str, float],
                      how: str, grace_h: float = 2.0) -> Plan:
    """Give a reactive policy its second chance. For each AT_RISK parcel it didn't act
    on that really does miss its connection, rebook it the way the policy would once
    the miss shows up in scans: `grace_h` after the late arrival, from the transfer
    hub, against the capacity its main plan left.

    `how`: "next_service" (published timetable, first service with room),
    "best" (observed delays, cheapest option by penalty) or "best_blind"
    (published timetable, cheapest option by penalty).

    This reads the true trip delays, but only to know *when* a reactive policy would
    notice: the future it would actually live through. The rebooking itself uses
    only the policy's own view."""
    t0 = time.perf_counter()
    legs = {l.leg_id: l for l in view.legs}
    ships = {s.shipment_id: s for s in view.shipments}
    left = leg_capacity(view)
    for o in plan.chosen.values():
        for leg in o.capacity_legs:
            kg, m3 = left[leg]
            left[leg] = (kg - o.weight_kg, m3 - o.volume_m3)
    events = []
    for d in dets:
        if d.kind != KIND_AT_RISK or d.shipment_id in plan.chosen:
            continue
        s = ships[d.shipment_id]
        _, miss = actual_scans(s, [legs[i] for i in s.planned_legs.split(";")], true_delays)
        if miss is not None:  # never strands -> a reactive policy never acts, rightly
            events.append((miss[1], d.shipment_id, d, miss[0]))
    if how not in ("next_service", "best", "best_blind"):
        raise ValueError(f"unknown follow-up {how!r}")
    first_service = how == "next_service"
    net = Network(view) if how == "best" else _blind(view)
    cfg = OptionsConfig(k_piggyback=1) if first_service else OptionsConfig()
    chosen = dict(plan.chosen)
    for t, sid, d, hub in sorted(events):
        later = replace(view, now=round(t + grace_h, 2))
        det = replace(d, kind=KIND_MISSED_CONNECTION, suspected_hub=hub)
        opts = generate_options(later, [det], cfg, network=net, capacity=left)[sid]
        if first_service:
            first = {}
            for o in opts:
                first.setdefault(o.mode, o)
            pick = first.get(MODE_PIGGYBACK) or first.get(MODE_LOCAL) or first[MODE_NONE]
        else:
            pick = min(opts, key=lambda o: o.penalty_inr)
        for leg in pick.capacity_legs:
            kg, m3 = left[leg]
            left[leg] = (kg - pick.weight_kg, m3 - pick.volume_m3)
        chosen[sid] = pick
    return Plan(plan.solver, chosen, round(sum(o.penalty_inr for o in chosen.values()), 2), plan.status,
                round(plan.solve_s + time.perf_counter() - t0, 3), plan.stats)


POLICIES: list[Policy] = [
    Policy("do_nothing", "baseline", "Do nothing", do_nothing),
    Policy("next_service", "baseline", "Next service, FCFS (TMS auto-rebook)", next_service,
           followup="next_service"),
    Policy("next_service_express_truck", "baseline", "Next service + truck for late EXPRESS",
           lambda v, d: next_service(v, d, express_truck=True), followup="next_service"),
    Policy("piggyback", "piggyback", "Piggyback (delay-aware, CP-SAT)", piggyback, followup="best"),
    Policy("piggyback_greedy", "ablation", "− optimizer (greedy)",
           lambda v, d: piggyback(v, d, optimizer="greedy"), followup="best"),
    Policy("piggyback_blind", "ablation", "− delay awareness (published timetable)",
           lambda v, d: piggyback(v, d, aware=False), followup="best_blind"),
    Policy("piggyback_proactive", "ablation", "+ proactive (rebooks AT_RISK early)",
           lambda v, d: piggyback(v, d, proactive=True)),
]
BY_NAME = {p.name: p for p in POLICIES}
