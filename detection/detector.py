"""Rule-based stranding detector.

For every shipment not yet delivered, compare its visible scans with the scans
its booked itinerary should have produced:

- a HELD scan as the latest event                       -> HELD
- any scan on a leg outside the itinerary               -> MISROUTE
- the parcel's (current or just finished) ride arrives too
  late for the next booked leg's departure:
    - that arrival is already in the past                -> MISSED_CONNECTION
    - it is still ahead                                  -> AT_RISK (not a stranding
      yet, so scoring ignores it; recovery can act early)
- the next expected scan is more than `grace_h` overdue:
    - expected an unload (parcel should be off a truck)  -> LOST_SCAN
    - expected a load the truck has visibly left without -> STALLED
- STALLED parcels that pile up at one hub within a few
  hours of each other                                    -> MISSORT (one cluster)

A lone missing scan may just be uploading late, so single stalls wait `grace_h`.
A pile-up at one hub is stronger evidence, so a burst is flagged once its
parcels are `burst_grace_h` overdue.

"Expected" times are timetable times shifted by each trip's observed delay. The
operator has no GPS, but every scan of any parcel on a trip shows how late
that trip is running. A trip nobody has scanned on yet may simply be late, so a
missing load only counts once some parcel was seen on that trip or the load is
more than `unobserved_trip_wait_h` overdue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from simulator.generator import planned_scans
from simulator.generator import rides as rides_of
from simulator.hubs import MIN_CONNECTION_H
from simulator.models import (
    SCAN_DELIVERED,
    SCAN_HELD,
    SCAN_INDUCTED,
    SCAN_LOADED,
    SCAN_UNLOADED,
    ScanEvent,
    TripLeg,
)

from .view import OperatorView, scans_by_shipment

KIND_HELD = "HELD"
KIND_MISROUTE = "MISROUTE"
KIND_LOST_SCAN = "LOST_SCAN"
KIND_MISSED_CONNECTION = "MISSED_CONNECTION"
KIND_MISSORT = "MISSORT"
KIND_STALLED = "STALLED"
KIND_AT_RISK = "AT_RISK"
NOT_STRANDED_KINDS = (KIND_AT_RISK,)


@dataclass(frozen=True)
class DetectorConfig:
    grace_h: float = 2.0          # how overdue the next scan must be before we flag
    burst_grace_h: float = 0.5    # the same, for parcels that are part of a MISSORT burst
    unobserved_trip_wait_h: float = 4.0  # overdue needed when nobody was seen on the trip
    cluster_min: int = 3          # stalls at one hub that make a MISSORT burst
    cluster_gap_h: float = 3.0    # max gap between consecutive stalls in a burst


@dataclass(frozen=True)
class Detection:
    shipment_id: str
    kind: str
    suspected_hub: str            # where we think the parcel physically is
    priority: str
    deadline_h: float
    last_scan_h: float
    expected_h: Optional[float]   # when the missing scan was due (None for HELD/MISROUTE)
    overdue_h: float
    confidence: float
    cluster_id: Optional[str]
    evidence: str


def observed_trip_delays(view: OperatorView) -> dict[str, float]:
    """How late each trip is running, from any parcel's load/unload scans on it."""
    legs = {l.leg_id: l for l in view.legs}
    out: dict[str, float] = {}
    for e in view.scans:
        if e.leg_id is None:
            continue
        leg = legs[e.leg_id]
        scheduled = leg.departure_h if e.event_type == SCAN_LOADED else leg.arrival_h
        out[leg.trip_id] = max(out.get(leg.trip_id, 0.0), e.ts_h - scheduled)
    return out


def _confidence(base: float, overdue: float, grace: float) -> float:
    return round(min(0.99, base + 0.05 * max(0.0, overdue - grace)), 2)


def detect(view: OperatorView, cfg: DetectorConfig = DetectorConfig()) -> list[Detection]:
    now = view.now
    legs = {l.leg_id: l for l in view.legs}
    delay = observed_trip_delays(view)
    by_ship = scans_by_shipment(view.scans)
    # Which legs other parcels were seen loading onto / unloading from.
    seen_loaded = {e.leg_id for e in view.scans if e.event_type == SCAN_LOADED}
    seen_unloaded = {e.leg_id for e in view.scans if e.event_type == SCAN_UNLOADED}

    out: list[Detection] = []
    stalls: list[Detection] = []
    for s in view.shipments:
        scans = by_ship.get(s.shipment_id)
        if not scans or scans[-1].event_type == SCAN_DELIVERED:
            continue
        last = scans[-1]
        path = [legs[i] for i in s.planned_legs.split(";")]

        def flag(kind, hub, expected, base, evidence, overdue=None):
            od = round(now - (expected if expected is not None else last.ts_h), 2) if overdue is None else overdue
            return Detection(s.shipment_id, kind, hub, s.priority, s.deadline_h, last.ts_h,
                             expected, od, _confidence(base, od, cfg.grace_h), None, evidence)

        if last.event_type == SCAN_HELD:
            out.append(flag(KIND_HELD, last.hub_id, None, 0.9, f"held: {last.note}"))
            continue

        planned_ids = {l.leg_id for l in path}
        off_plan = [e for e in scans if e.leg_id is not None and e.leg_id not in planned_ids]
        if off_plan:
            wrong = legs[off_plan[-1].leg_id]
            out.append(flag(KIND_MISROUTE, wrong.to_hub, None, 0.95,
                            f"on unbooked leg {wrong.leg_id} {wrong.from_hub}>{wrong.to_hub}"))
            continue

        nxt = _next_planned(s, path, last)
        if nxt is None or nxt[2] == SCAN_DELIVERED:
            continue  # at destination; delivery is not a recovery problem
        leg = legs[nxt[3]]
        expected = round(nxt[0] + delay.get(leg.trip_id, 0.0), 2)
        overdue = round(now - expected, 2)

        miss = _connection_miss(path, last, nxt, legs, delay)
        if miss is not None:
            hub, arrival, onward = miss
            if arrival <= now:
                out.append(flag(KIND_MISSED_CONNECTION, hub, arrival, 0.9,
                                f"arrived {hub} ~{arrival:.2f}h, {onward.leg_id} left "
                                f"~{onward.departure_h + delay.get(onward.trip_id, 0.0):.2f}h",
                                round(now - arrival, 2)))
            else:
                out.append(flag(KIND_AT_RISK, hub, arrival, 0.7,
                                f"running late; due {hub} ~{arrival:.2f}h, too late for {onward.leg_id}",
                                0.0))
            continue
        if overdue < min(cfg.grace_h, cfg.burst_grace_h):
            continue

        trip_seen = leg.trip_id in delay
        if nxt[2] == SCAN_UNLOADED:
            if overdue < cfg.grace_h:
                continue
            arrived = leg.leg_id in seen_unloaded
            out.append(flag(KIND_LOST_SCAN, leg.to_hub, expected, 0.75 if arrived else 0.5,
                            f"no unload from {leg.leg_id}; "
                            + ("other parcels were unloaded from it" if arrived else "no parcel unloaded from it yet"),
                            overdue))
        elif trip_seen or overdue >= cfg.unobserved_trip_wait_h:
            left = leg.leg_id in seen_loaded
            stalls.append(flag(KIND_STALLED, last.hub_id, expected, 0.6 if left else 0.4,
                               f"not loaded onto {leg.leg_id}"
                               + ("; other parcels were" if left else "")
                               + ("" if trip_seen else "; nobody seen on that trip"), overdue))

    out.extend(_cluster_stalls(stalls, by_ship, cfg))
    out.sort(key=lambda d: d.shipment_id)
    return out


def _connection_miss(path: list[TripLeg], last: ScanEvent, nxt, legs: dict[str, TripLeg],
                     delay: dict[str, float]):
    """If the ride the parcel is on (or just got off) arrives too late for the next
    booked leg, return (transfer hub, arrival time, the leg it misses)."""
    rides = rides_of(path)
    if last.event_type == SCAN_LOADED:          # on board; arrival is predicted
        idx = next(i for i, (b, _) in enumerate(rides) if b.leg_id == last.leg_id)
        alight = rides[idx][1]
        arrival = alight.arrival_h + delay.get(alight.trip_id, 0.0)
    elif last.event_type == SCAN_UNLOADED and nxt[2] == SCAN_LOADED:
        idx = next(i for i, (_, a) in enumerate(rides) if a.leg_id == last.leg_id)
        alight, arrival = rides[idx][1], last.ts_h
    else:
        return None
    if idx + 1 >= len(rides):
        return None
    onward = rides[idx + 1][0]
    if arrival + MIN_CONNECTION_H > onward.departure_h + delay.get(onward.trip_id, 0.0) + 1e-9:
        return alight.to_hub, round(arrival, 2), onward
    return None


def _next_planned(s, path: list[TripLeg], last: ScanEvent):
    plan = planned_scans(s, path)
    for i, p in enumerate(plan):
        if p[2] == last.event_type and (p[2] == SCAN_INDUCTED or p[3] == last.leg_id):
            return plan[i + 1] if i + 1 < len(plan) else None
    return None


def _cluster_stalls(stalls: list[Detection], by_ship, cfg: DetectorConfig) -> list[Detection]:
    """Stalls after an unload at a transfer hub, close together in time, are a burst."""
    at_hub: dict[str, list[Detection]] = {}
    out = []
    for d in stalls:
        if by_ship[d.shipment_id][-1].event_type == SCAN_UNLOADED:
            at_hub.setdefault(d.suspected_hub, []).append(d)
        elif d.overdue_h >= cfg.grace_h:
            out.append(d)
    for hub, ds in at_hub.items():
        ds.sort(key=lambda d: d.last_scan_h)
        group = [ds[0]]
        for d in ds[1:] + [None]:
            if d is not None and d.last_scan_h - group[-1].last_scan_h <= cfg.cluster_gap_h:
                group.append(d)
                continue
            if len(group) >= cfg.cluster_min:
                cid = f"{hub}@{group[0].last_scan_h:.1f}h"
                out.extend(Detection(**{**g.__dict__, "kind": KIND_MISSORT, "cluster_id": cid,
                                        "confidence": max(g.confidence, 0.85),
                                        "evidence": f"{g.evidence}; {len(group)} parcels stuck at {hub}"})
                           for g in group)
            else:
                out.extend(g for g in group if g.overdue_h >= cfg.grace_h)
            if d is not None:
                group = [d]
    return out
