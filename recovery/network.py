"""The timetable as the planner believes it at `now`.

Each leg gets an effective departure/arrival: its timetable time, shifted by the
trip's observed delay (from scans, see detection) and by any known LANE_DELAY
it runs into, which carries on to the rest of that trip. Legs after a trip's
breakdown never complete and are dropped. A closed hub doesn't stop trucks
passing through, but nothing can be loaded or unloaded there while it is closed.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from detection.detector import observed_trip_delays
from detection.view import OperatorView
from simulator.hubs import MIN_CONNECTION_H
from simulator.models import (
    DISRUPTION_HUB_CLOSURE,
    DISRUPTION_LANE_DELAY,
    DISRUPTION_TRIP_BREAKDOWN,
)


@dataclass(frozen=True)
class EffLeg:
    leg_id: str
    trip_id: str
    seq: int
    from_hub: str
    to_hub: str
    dep: float
    arr: float
    spare_kg: float
    spare_m3: float
    distance_km: float
    can_board: bool = True        # from_hub open at departure
    can_alight: bool = True       # to_hub open at arrival


class Network:
    def __init__(self, view: OperatorView, trip_delays: Optional[dict[str, float]] = None,
                 disruptions: bool = True):
        """`trip_delays` overrides the observed delays (the replay passes the true ones;
        `{}` means the published timetable). `disruptions=False` ignores known
        disruptions. Both together give the delay-blind view a typical TMS plans on."""
        self.now = view.now
        delay = observed_trip_delays(view) if trip_delays is None else trip_delays
        lane_km = {l.lane_id: l.distance_km for l in view.lanes}
        known = view.disruptions if disruptions else []
        lane_delays = [d for d in known if d.kind == DISRUPTION_LANE_DELAY]
        breakdown = {d.trip_id: d.start_h for d in known if d.kind == DISRUPTION_TRIP_BREAKDOWN}
        self.closures = [(d.hub_id, d.start_h, d.end_h) for d in known if d.kind == DISRUPTION_HUB_CLOSURE]

        by_trip: dict[str, list] = {}
        for l in view.legs:
            by_trip.setdefault(l.trip_id, []).append(l)

        legs: list[EffLeg] = []
        for trip_id, tl in by_trip.items():
            shift = delay.get(trip_id, 0.0)
            for l in sorted(tl, key=lambda l: l.seq):
                dep = l.departure_h + shift
                for d in lane_delays:
                    if d.lane_id == l.lane_id and d.start_h <= dep <= d.end_h:
                        shift += d.delay_h
                arr = l.arrival_h + shift
                if trip_id in breakdown and arr > breakdown[trip_id]:
                    break  # this leg and everything after it never completes
                legs.append(EffLeg(l.leg_id, trip_id, l.seq, l.from_hub, l.to_hub,
                                   round(dep, 2), round(arr, 2), l.spare_kg, l.spare_m3,
                                   lane_km[l.lane_id],
                                   not self.hub_closed(l.from_hub, dep),
                                   not self.hub_closed(l.to_hub, arr)))
        self.legs = sorted(legs, key=lambda e: (e.dep, e.leg_id))
        self.deps = [e.dep for e in self.legs]
        self.by_id = {e.leg_id: e for e in self.legs}

    def hub_closed(self, hub: str, t: float) -> bool:
        return any(h == hub and s <= t <= e for h, s, e in self.closures)

    def earliest_path(self, origin: str, dest: str, ready_h: float,
                      usable: Callable[[EffLeg], bool] = lambda e: True,
                      banned_trips: Iterable[str] = ()) -> Optional[list[EffLeg]]:
        """Earliest-arrival itinerary (connection scan). Staying aboard a trip needs no
        connection time; changing trips needs MIN_CONNECTION_H."""
        banned = set(banned_trips)
        ready = {origin: ready_h}
        arrived_by: dict[str, EffLeg] = {}
        boarded_at: dict[str, EffLeg] = {}
        best = math.inf
        for e in self.legs[bisect.bisect_left(self.deps, ready_h):]:
            if e.dep >= best:
                break
            if e.trip_id in banned or not usable(e):
                continue
            on_trip = e.trip_id in boarded_at
            if not on_trip and (not e.can_board or ready.get(e.from_hub, math.inf) > e.dep):
                continue
            if not on_trip:
                boarded_at[e.trip_id] = e
            if not e.can_alight:
                continue  # stay aboard through a closed hub
            at = e.arr + (0.0 if e.to_hub == dest else MIN_CONNECTION_H)
            if at < ready.get(e.to_hub, math.inf) and e.to_hub != origin:
                ready[e.to_hub] = at
                arrived_by[e.to_hub] = e
                if e.to_hub == dest:
                    best = at
        if dest not in arrived_by:
            return None
        path: list[EffLeg] = []
        hub = dest
        while hub != origin:
            last = arrived_by[hub]
            first = boarded_at[last.trip_id]
            ride = [x for x in self._trip(last.trip_id) if first.seq <= x.seq <= last.seq]
            path = ride + path
            hub = first.from_hub
        return path

    def _trip(self, trip_id: str) -> list[EffLeg]:
        if not hasattr(self, "_by_trip"):
            self._by_trip: dict[str, list[EffLeg]] = {}
            for e in sorted(self.legs, key=lambda e: (e.trip_id, e.seq)):
                self._by_trip.setdefault(e.trip_id, []).append(e)
        return self._by_trip[trip_id]
