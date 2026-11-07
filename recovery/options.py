"""Recovery options for flagged shipments (step 3).

For each detection the parcel is assumed to be at the detector's suspected hub,
ready to move after a handling time that depends on what went wrong (a held
parcel needs releasing, a lost one needs finding). From there it can:

- PIGGYBACK on up to `k_piggyback` scheduled itineraries with spare room
  (alternatives come from banning the previous option's first trip),
- go by DEDICATED vehicle straight to its destination,
- be delivered LOCALly if it is already at its destination hub, or
- NONE: leave it, which costs `unrecovered_lateness_h` priority-weighted hours.

Every option carries its money cost and its penalty: cost plus
`value_of_hour_inr` x priority weight x hours late. The optimizer minimises the
total penalty.
"""

from __future__ import annotations

from dataclasses import dataclass

from detection.detector import (
    KIND_AT_RISK,
    KIND_HELD,
    KIND_LOST_SCAN,
    KIND_MISROUTE,
    KIND_MISSED_CONNECTION,
    KIND_MISSORT,
    KIND_STALLED,
    Detection,
)
from detection.view import OperatorView
from simulator.generator import DELIVERY_H
from simulator.hubs import MIN_CONNECTION_H, VEHICLE_TYPES, road_hours, road_km

from .network import EffLeg, Network

MODE_PIGGYBACK = "PIGGYBACK"
MODE_DEDICATED = "DEDICATED"
MODE_LOCAL = "LOCAL"
MODE_NONE = "NONE"

PRIORITY_WEIGHT = {"EXPRESS": 3.0, "STANDARD": 2.0, "ECONOMY": 1.0}

# Hours from `now` until the parcel can move again, by what went wrong.
HANDLING_H = {
    KIND_HELD: 2.0,                # paperwork / repack before release
    KIND_LOST_SCAN: 1.5,           # find it on the floor first
    KIND_MISSORT: 1.0,             # pull it out of the wrong bay
    KIND_STALLED: 1.0,
    KIND_MISROUTE: 0.5,            # it is where the wrong truck dropped it
    KIND_MISSED_CONNECTION: 0.5,   # already sorted, just missed the truck
}


@dataclass(frozen=True)
class OptionsConfig:
    k_piggyback: int = 3
    handoff_cost_inr: float = 60.0              # handling per load onto a trip
    marginal_inr_per_kg_km: float = 0.004       # fuel/wear of extra weight on a running truck
    dedicated_dispatch_h: float = 1.5           # time to get a vehicle and driver
    unrecovered_lateness_h: float = 48.0        # what "do nothing" is charged as
    value_of_hour_inr: float = 250.0            # per priority-weighted hour late


@dataclass(frozen=True)
class RecoveryOption:
    option_id: str
    shipment_id: str
    mode: str
    from_hub: str
    ready_h: float
    legs: tuple[str, ...]           # trip legs ridden (PIGGYBACK only)
    capacity_legs: tuple[str, ...]  # legs that use spare capacity (not the shipment's own booking)
    depart_h: float | None
    delivered_h: float | None
    lateness_h: float
    cost_inr: float
    penalty_inr: float
    weight_kg: float
    volume_m3: float
    note: str


def ready_time(det: Detection, now: float) -> float:
    if det.kind == KIND_AT_RISK:  # will arrive late; can be re-sorted on arrival
        return round(max(now, det.expected_h) + MIN_CONNECTION_H, 2)
    return round(now + HANDLING_H.get(det.kind, 1.0), 2)


def generate_options(view: OperatorView, detections: list[Detection],
                     cfg: OptionsConfig = OptionsConfig(),
                     network: Network | None = None,
                     capacity: dict[str, tuple[float, float]] | None = None,
                     ) -> dict[str, list[RecoveryOption]]:
    """`capacity` (leg -> spare kg, m3) overrides the timetable's spare capacity,
    e.g. with what is left after accepted recoveries took their share."""
    net = network or Network(view)
    hubs = {h.hub_id: h for h in view.hubs}
    ships = {s.shipment_id: s for s in view.shipments}
    out: dict[str, list[RecoveryOption]] = {}

    for det in detections:
        s = ships[det.shipment_id]
        w = PRIORITY_WEIGHT[s.priority]
        origin, ready = det.suspected_hub, ready_time(det, view.now)
        own = set(s.planned_legs.split(";"))
        opts: list[RecoveryOption] = []

        def add(mode, legs, capacity_legs, depart, delivered, cost, note):
            late = round(max(0.0, delivered - s.deadline_h), 2) if delivered is not None \
                else cfg.unrecovered_lateness_h
            opts.append(RecoveryOption(
                f"{s.shipment_id}:{mode}:{len(opts)}", s.shipment_id, mode, origin, ready,
                tuple(legs), tuple(capacity_legs),
                None if depart is None else round(depart, 2),
                None if delivered is None else round(delivered, 2),
                late, round(cost, 2), round(cost + cfg.value_of_hour_inr * w * late, 2),
                s.weight_kg, s.volume_m3, note))

        if origin == s.dest_hub:
            add(MODE_LOCAL, (), (), ready, ready + DELIVERY_H, 0.0, "already at destination")
        else:
            def fits(e: EffLeg) -> bool:
                if e.leg_id in own:
                    return True
                kg, m3 = capacity[e.leg_id] if capacity is not None else (e.spare_kg, e.spare_m3)
                return kg >= s.weight_kg and m3 >= s.volume_m3

            banned: list[str] = []
            seen: set[tuple[str, ...]] = set()
            for _ in range(cfg.k_piggyback):
                path = net.earliest_path(origin, s.dest_hub, ready, fits, banned)
                if path is None:
                    break
                ids = tuple(e.leg_id for e in path)
                banned.append(path[0].trip_id)
                if ids in seen:
                    continue
                seen.add(ids)
                handoffs = 1 + sum(a.trip_id != b.trip_id for a, b in zip(path, path[1:]))
                cost = handoffs * cfg.handoff_cost_inr + sum(
                    e.distance_km for e in path if e.leg_id not in own) * s.weight_kg * cfg.marginal_inr_per_kg_km
                add(MODE_PIGGYBACK, ids, tuple(i for i in ids if i not in own), path[0].dep,
                    path[-1].arr + DELIVERY_H, cost,
                    f"{handoffs} trip(s) via " + ">".join([path[0].from_hub] + [e.to_hub for e in path]))

            vehicle = next((v for v in VEHICLE_TYPES
                            if v.capacity_kg >= s.weight_kg and v.capacity_m3 >= s.volume_m3), None)
            if vehicle is not None:
                km = road_km(hubs[origin], hubs[s.dest_hub])
                depart = ready + cfg.dedicated_dispatch_h
                add(MODE_DEDICATED, (), (), depart, depart + road_hours(km) + DELIVERY_H,
                    km * vehicle.cost_per_km_inr, f"{vehicle.code}, {km:.0f} km")

        add(MODE_NONE, (), (), None, None, 0.0, "leave it")
        out[s.shipment_id] = opts
    return out
