"""Replay a recovery plan against what really happened (step 5's scorer).

The planner only knew observed trip delays and the detector's guess of where a
parcel is. The replay uses the truth: the real trip delays (`sim_trip_delays`)
and the parcel's real hub (`sim_truth`). A plan can therefore fail. The parcel
may not be at the hub we sent the truck to, or a connection may break because
a trip ran later than anyone had seen.

Parcels flagged AT_RISK are checked against their real future: if, with the
true delays, they do miss their connection they count as stranded; otherwise
any action taken on them was unnecessary.
"""

from __future__ import annotations

from dataclasses import dataclass

from detection.detector import KIND_AT_RISK, Detection
from detection.view import OperatorView
from simulator.generator import DELIVERY_H, actual_scans
from simulator.hubs import MIN_CONNECTION_H
from simulator.models import TruthRecord

from .network import Network
from .optimizer import Plan
from .options import (
    MODE_DEDICATED,
    MODE_NONE,
    MODE_PIGGYBACK,
    PRIORITY_WEIGHT,
    OptionsConfig,
)

ON_TIME = "ON_TIME"
LATE = "LATE"
FAILED = "FAILED"              # acted, but the plan could not work in reality
UNRECOVERED = "UNRECOVERED"    # stranded and nothing (useful) was done
UNNECESSARY = "UNNECESSARY"    # acted on a parcel that was fine
NOT_NEEDED = "NOT_NEEDED"      # flagged a parcel that was fine, and left it


@dataclass(frozen=True)
class Outcome:
    shipment_id: str
    stranded: bool
    status: str
    mode: str
    delivered_h: float | None
    lateness_h: float
    cost_inr: float
    reason: str


def replay(view: OperatorView, truth: list[TruthRecord], trip_delays: dict,
           detections: list[Detection], plan: Plan,
           cfg: OptionsConfig = OptionsConfig()) -> tuple[list[Outcome], dict]:
    real = Network(view, trip_delays)
    legs = {l.leg_id: l for l in view.legs}
    ships = {s.shipment_id: s for s in view.shipments}
    truth_by = {t.shipment_id: t for t in truth}
    det_by = {d.shipment_id: d for d in detections}

    outcomes: list[Outcome] = []
    for sid in sorted(truth_by.keys() | det_by.keys()):
        s = ships[sid]
        opt = plan.chosen.get(sid)
        mode = opt.mode if opt else MODE_NONE
        cost = opt.cost_inr if opt else 0.0

        # Where is the parcel really, and from when can it move?
        true_hub, true_ready = None, None
        if sid in truth_by:
            true_hub = truth_by[sid].true_hub_id
        elif sid in det_by and det_by[sid].kind == KIND_AT_RISK:
            _, miss = actual_scans(s, [legs[i] for i in s.planned_legs.split(";")], trip_delays)
            if miss is not None:
                true_hub, true_ready = miss[0], miss[1] + MIN_CONNECTION_H

        def done(status, delivered=None, reason=""):
            if status in (ON_TIME, LATE):
                late = round(max(0.0, delivered - s.deadline_h), 2)
            elif status in (FAILED, UNRECOVERED):
                late = cfg.unrecovered_lateness_h
            else:
                late = 0.0
            outcomes.append(Outcome(sid, true_hub is not None, status, mode, delivered, late,
                                    0.0 if mode == MODE_NONE else cost, reason))

        if true_hub is None:
            done(UNNECESSARY if mode != MODE_NONE else NOT_NEEDED, reason="parcel was fine")
            continue
        if sid not in det_by:
            done(UNRECOVERED, reason="not detected")
            continue
        if mode == MODE_NONE:
            done(UNRECOVERED, reason="left")
            continue
        if opt.from_hub != true_hub:
            done(FAILED, reason=f"parcel is at {true_hub}, not {opt.from_hub}")
            continue

        ready = max(opt.ready_h, true_ready or 0.0)
        if mode == MODE_PIGGYBACK:
            delivered, why = _ride(real, opt.legs, ready)
            if delivered is None:
                done(FAILED, reason=why)
                continue
        elif mode == MODE_DEDICATED:
            delivered = opt.delivered_h + (ready - opt.ready_h)
        else:  # LOCAL
            delivered = ready + DELIVERY_H
        delivered = round(delivered, 2)
        done(ON_TIME if delivered <= s.deadline_h else LATE, delivered)

    return outcomes, kpis(outcomes, ships, cfg)


def _ride(real: Network, leg_ids: tuple[str, ...], ready: float) -> tuple[float | None, str]:
    """Follow the booked legs with the true timetable; return the delivery time or why not."""
    t = ready
    prev = None
    for lid in leg_ids:
        e = real.by_id.get(lid)
        if e is None:
            return None, f"{lid} never ran"
        same_trip = prev is not None and prev.trip_id == e.trip_id
        if not same_trip:
            if not e.can_board:
                return None, f"{e.from_hub} closed at {lid} departure"
            if e.dep < t - 1e-9:
                return None, f"{lid} left at {e.dep:.2f}h, parcel ready {t:.2f}h"
        prev = e
        t = e.arr + MIN_CONNECTION_H
    if not prev.can_alight:
        return None, f"{prev.to_hub} closed at arrival"
    return prev.arr + DELIVERY_H, ""


def kpis(outcomes: list[Outcome], ships, cfg: OptionsConfig) -> dict:
    stranded = [o for o in outcomes if o.stranded]
    count = lambda st: sum(o.status == st for o in stranded)  # noqa: E731
    weighted_late = sum(PRIORITY_WEIGHT[ships[o.shipment_id].priority] * o.lateness_h for o in stranded)
    cost = sum(o.cost_inr for o in outcomes)
    wasted = [o for o in outcomes if o.status == UNNECESSARY]
    return {
        "stranded": len(stranded),
        "on_time": count(ON_TIME),
        "late": count(LATE),
        "failed": count(FAILED),
        "unrecovered": count(UNRECOVERED),
        "on_time_rate": round(count(ON_TIME) / len(stranded), 3) if stranded else 1.0,
        "weighted_late_h": round(weighted_late, 1),
        "cost_inr": round(cost),
        "unnecessary": len(wasted),
        "unnecessary_cost_inr": round(sum(o.cost_inr for o in wasted)),
        "realized_penalty_inr": round(cost + cfg.value_of_hour_inr * weighted_late),
        "by_mode": {m: sum(o.mode == m for o in stranded if o.status in (ON_TIME, LATE))
                    for m in (MODE_PIGGYBACK, MODE_DEDICATED, "LOCAL")},
    }
