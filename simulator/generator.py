"""Seeded scenario generator.

`generate_scenario(seed=...)` builds hubs -> lanes -> corridors -> trips/legs ->
shipments (booked onto legs) -> scan histories -> strandings -> disruptions.
Everything comes from one `random.Random(seed)`, so a seed and a config always
reproduce the same scenario byte for byte.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import replace
from typing import Optional

from .hubs import (
    HUB_DWELL_H,
    HUBS,
    MIN_CONNECTION_H,
    VEHICLE_TYPES,
    road_hours,
    road_km,
)
from .models import (
    ANOMALY_HELD,
    ANOMALY_LOST_SCAN,
    ANOMALY_MISROUTE,
    ANOMALY_MISSORT,
    DISRUPTION_HUB_CLOSURE,
    DISRUPTION_LANE_DELAY,
    DISRUPTION_TRIP_BREAKDOWN,
    PRIORITIES,
    SCAN_DELIVERED,
    SCAN_HELD,
    SCAN_INDUCTED,
    SCAN_LOADED,
    SCAN_UNLOADED,
    Corridor,
    Disruption,
    Hub,
    Lane,
    Scenario,
    ScanEvent,
    ScenarioConfig,
    Shipment,
    Trip,
    TripLeg,
    TruthRecord,
)

N_NEAREST = 3
N_CORRIDORS = 80
INDUCTION_H = 1.0            # created -> ready to load at origin
DELIVERY_H = 0.5             # unloaded at destination -> delivered scan
PRIORITY_WEIGHTS = (0.2, 0.55, 0.25)
# deadline = created + fixed + slack * planned transit time
DEADLINE_RULE = {"EXPRESS": (2.0, 1.15), "STANDARD": (4.0, 1.4), "ECONOMY": (8.0, 1.8)}
HOLD_REASONS = ("DOCS_MISSING", "DAMAGED_PACKAGING", "ADDRESS_QUERY", "CUSTOMS_CHECK")


def _r(x: float, nd: int = 2) -> float:
    return round(x, nd)


# --------------------------------------------------------------------------- network

def build_lanes(hubs: list[Hub]) -> list[Lane]:
    """MST (so the graph is connected) plus each hub's 3 nearest neighbours, both directions."""
    n = len(hubs)
    dist = [[road_km(a, b) for b in hubs] for a in hubs]
    edges: set[tuple[int, int]] = set()

    # Prim's MST
    in_tree = [False] * n
    best = [math.inf] * n
    parent = [-1] * n
    best[0] = 0.0
    for _ in range(n):
        u = min((i for i in range(n) if not in_tree[i]), key=lambda i: best[i])
        in_tree[u] = True
        if parent[u] >= 0:
            edges.add((min(u, parent[u]), max(u, parent[u])))
        for v in range(n):
            if not in_tree[v] and dist[u][v] < best[v]:
                best[v], parent[v] = dist[u][v], u

    for i in range(n):
        nearest = sorted((j for j in range(n) if j != i), key=lambda j: dist[i][j])[:N_NEAREST]
        for j in nearest:
            edges.add((min(i, j), max(i, j)))

    lanes = []
    for i, j in sorted(edges):
        for a, b in ((i, j), (j, i)):
            km = dist[a][b]
            lanes.append(Lane(f"{hubs[a].hub_id}-{hubs[b].hub_id}", hubs[a].hub_id, hubs[b].hub_id,
                              _r(km, 1), _r(road_hours(km))))
    return lanes


def build_corridors(rng: random.Random, hubs: list[Hub], lanes: list[Lane]) -> list[Corridor]:
    """Every lane is covered by at least one corridor; extra random corridors up to N_CORRIDORS."""
    out_lanes: dict[str, list[Lane]] = {h.hub_id: [] for h in hubs}
    for lane in lanes:
        out_lanes[lane.from_hub].append(lane)

    def extend(first: Lane) -> list[Lane]:
        path = [first]
        visited = {first.from_hub, first.to_hub}
        for _ in range(rng.choice((0, 1, 1, 2))):
            nxt = [l for l in out_lanes[path[-1].to_hub] if l.to_hub not in visited]
            if not nxt:
                break
            lane = rng.choice(nxt)
            path.append(lane)
            visited.add(lane.to_hub)
        return path

    paths: list[list[Lane]] = []
    seen: set[str] = set()
    covered: set[str] = set()
    order = lanes[:]
    rng.shuffle(order)
    for lane in order:
        if lane.lane_id in covered:
            continue
        path = extend(lane)
        key = ">".join([path[0].from_hub] + [l.to_hub for l in path])
        if key in seen:
            path, key = [lane], f"{lane.from_hub}>{lane.to_hub}"
        seen.add(key)
        paths.append(path)
        covered.update(l.lane_id for l in path)
    attempts = 0
    while len(paths) < N_CORRIDORS and attempts < 10_000:
        attempts += 1
        path = extend(rng.choice(lanes))
        key = ">".join([path[0].from_hub] + [l.to_hub for l in path])
        if key not in seen:
            seen.add(key)
            paths.append(path)

    corridors = []
    for i, path in enumerate(paths, start=1):
        vt = rng.choices(VEHICLE_TYPES, weights=(0.3, 0.4, 0.3))[0]
        freq = rng.choice((12, 24, 24))
        corridors.append(Corridor(
            corridor_id=f"C{i:03d}",
            hub_sequence=">".join([path[0].from_hub] + [l.to_hub for l in path]),
            vehicle_type=vt.code,
            frequency_h=freq,
            first_departure_h=round(rng.uniform(0, freq) * 4) / 4,
            capacity_kg=vt.capacity_kg,
            capacity_m3=vt.capacity_m3,
            cost_per_km_inr=vt.cost_per_km_inr,
        ))
    return corridors


def build_trips(rng: random.Random, cfg: ScenarioConfig, corridors: list[Corridor],
                lanes: list[Lane]) -> tuple[list[Trip], list[TripLeg]]:
    lane_by_id = {l.lane_id: l for l in lanes}
    # Mean spare fraction falls from ~0.58 (slack network) to ~0.03 (saturated).
    mean = 0.55 * (1.0 - cfg.capacity_tightness) + 0.03
    a = 2.0
    b = a * (1.0 - mean) / mean

    trips, legs = [], []
    n = 0
    for c in corridors:
        stops = c.hub_sequence.split(">")
        dep = c.first_departure_h
        while dep < cfg.horizon_h:
            n += 1
            trip_id = f"T{n:04d}"
            t = dep
            for seq, (fh, th) in enumerate(zip(stops, stops[1:]), start=1):
                lane = lane_by_id[f"{fh}-{th}"]
                frac = rng.betavariate(a, b)
                arr = t + lane.travel_h
                legs.append(TripLeg(
                    leg_id=f"{trip_id}-{seq}", trip_id=trip_id, seq=seq, lane_id=lane.lane_id,
                    from_hub=fh, to_hub=th, departure_h=_r(t), arrival_h=_r(arr),
                    spare_kg=_r(c.capacity_kg * frac, 1),
                    spare_m3=_r(min(c.capacity_m3, c.capacity_m3 * frac * rng.uniform(0.8, 1.2)), 2),
                ))
                t = arr + HUB_DWELL_H
            trips.append(Trip(trip_id, c.corridor_id, _r(dep), _r(t - HUB_DWELL_H)))
            dep += c.frequency_h
    return trips, legs


# --------------------------------------------------------------------------- routing

class Router:
    """Earliest-arrival routing over trip legs (connection scan)."""

    def __init__(self, legs: list[TripLeg]):
        self.legs = sorted(legs, key=lambda l: (l.departure_h, l.leg_id))
        self.deps = [l.departure_h for l in self.legs]

    def route(self, origin: str, dest: str, ready_h: float) -> Optional[list[TripLeg]]:
        ready = {origin: ready_h}           # time a parcel can be loaded at a hub
        arrived_by: dict[str, TripLeg] = {}
        boarded_at: dict[str, TripLeg] = {}  # trip_id -> leg where we got on
        best_dest = math.inf
        for leg in self.legs[bisect.bisect_left(self.deps, ready_h):]:
            if leg.departure_h >= best_dest:
                break
            on_trip = leg.trip_id in boarded_at
            if not on_trip and ready.get(leg.from_hub, math.inf) > leg.departure_h:
                continue
            if not on_trip:
                boarded_at[leg.trip_id] = leg
            # Staying aboard needs no connection time; transferring off needs MIN_CONNECTION_H.
            at = leg.arrival_h + (0.0 if leg.to_hub == dest else MIN_CONNECTION_H)
            if at < ready.get(leg.to_hub, math.inf) and leg.to_hub != origin:
                ready[leg.to_hub] = at
                arrived_by[leg.to_hub] = leg
                if leg.to_hub == dest:
                    best_dest = at
        if dest not in arrived_by:
            return None

        path: list[TripLeg] = []
        hub = dest
        while hub != origin:
            last = arrived_by[hub]
            first = boarded_at[last.trip_id]
            ride = [l for l in self._trip_legs(last.trip_id) if first.seq <= l.seq <= last.seq]
            path = ride + path
            hub = first.from_hub
        return path

    def _trip_legs(self, trip_id: str) -> list[TripLeg]:
        if not hasattr(self, "_by_trip"):
            self._by_trip: dict[str, list[TripLeg]] = {}
            for l in sorted(self.legs, key=lambda l: (l.trip_id, l.seq)):
                self._by_trip.setdefault(l.trip_id, []).append(l)
        return self._by_trip[trip_id]


def rides(path: list[TripLeg]) -> list[tuple[TripLeg, TripLeg]]:
    """Group consecutive legs of the same trip into (board leg, alight leg)."""
    out: list[tuple[TripLeg, TripLeg]] = []
    for leg in path:
        if out and out[-1][1].trip_id == leg.trip_id:
            out[-1] = (out[-1][0], leg)
        else:
            out.append((leg, leg))
    return out


# --------------------------------------------------------------------------- shipments

# An unnumbered scan: (ts_h, hub_id, event_type, leg_id, note)
RawScan = tuple[float, str, str, Optional[str], Optional[str]]


def planned_scans(s: Shipment, path: list[TripLeg]) -> list[RawScan]:
    scans: list[RawScan] = [(s.created_h, s.origin_hub, SCAN_INDUCTED, None, None)]
    for board, alight in rides(path):
        scans.append((board.departure_h, board.from_hub, SCAN_LOADED, board.leg_id, None))
        scans.append((alight.arrival_h, alight.to_hub, SCAN_UNLOADED, alight.leg_id, None))
    scans.append((_r(path[-1].arrival_h + DELIVERY_H), s.dest_hub, SCAN_DELIVERED, None, None))
    return scans


def build_shipments(rng: random.Random, cfg: ScenarioConfig, hubs: list[Hub], router: Router,
                    ) -> tuple[list[Shipment], dict[str, list[TripLeg]]]:
    # Bigger hubs send and receive more freight.
    weights = [h.sort_capacity_per_h for h in hubs]
    shipments, paths = [], {}
    i = 0
    while len(shipments) < cfg.n_shipments:
        i += 1
        if i > cfg.n_shipments * 20:
            raise RuntimeError("could not route enough shipments; network too sparse")
        o, d = rng.choices(hubs, weights=weights, k=2)
        if o.hub_id == d.hub_id:
            continue
        created = _r(rng.uniform(0.0, cfg.now_h - 1.0))
        path = router.route(o.hub_id, d.hub_id, created + INDUCTION_H)
        if path is None:
            continue
        priority = rng.choices(PRIORITIES, weights=PRIORITY_WEIGHTS)[0]
        fixed, slack = DEADLINE_RULE[priority]
        arrival = _r(path[-1].arrival_h + DELIVERY_H)
        weight = min(400.0, max(0.2, rng.lognormvariate(math.log(8.0), 1.0)))
        sid = f"S{len(shipments) + 1:05d}"
        shipments.append(Shipment(
            shipment_id=sid, origin_hub=o.hub_id, dest_hub=d.hub_id, priority=priority,
            weight_kg=_r(weight, 1), volume_m3=_r(weight / rng.uniform(120.0, 300.0), 3),
            created_h=created, deadline_h=_r(created + fixed + slack * (arrival - created)),
            planned_legs=";".join(l.leg_id for l in path), planned_arrival_h=arrival,
        ))
        paths[sid] = path
    return shipments, paths


# --------------------------------------------------------------------------- strandings

def _strand_held(rng, s, scans, now):
    # Hold after an induction or unload that happened at least an hour before now.
    idx = [k for k, sc in enumerate(scans)
           if sc[2] in (SCAN_INDUCTED, SCAN_UNLOADED) and sc[0] < now - 1.5 and sc[1] != s.dest_hub]
    if not idx:
        return None
    k = rng.choice(idx)
    ts, hub = scans[k][0], scans[k][1]
    held_at = _r(min(now - 0.25, ts + rng.uniform(0.3, 1.5)))
    kept = scans[:k + 1] + [(held_at, hub, SCAN_HELD, None, rng.choice(HOLD_REASONS))]
    return kept, TruthRecord(s.shipment_id, ANOMALY_HELD, held_at, hub)


def _strand_misroute(rng, s, scans, now, path, legs_from_hub):
    planned_hubs = {s.origin_hub} | {l.to_hub for l in path}
    idx =[k for k, sc in enumerate(scans) if sc[2] == SCAN_LOADED and sc[0] < now - 1.0]
    rng.shuffle(idx)
    for k in idx:
        ts, hub = scans[k][0], scans[k][1]
        earliest = max(ts - 2.0, scans[k - 1][0] + 0.5)  # after the parcel reached this hub
        wrong = [l for l in legs_from_hub.get(hub, [])
                 if earliest <= l.departure_h <= ts + 3.0 and l.to_hub not in planned_hubs
                 and l.arrival_h < now]
        if wrong:
            leg = rng.choice(wrong)
            kept = scans[:k] + [(leg.departure_h, hub, SCAN_LOADED, leg.leg_id, None),
                                (leg.arrival_h, leg.to_hub, SCAN_UNLOADED, leg.leg_id, None)]
            return kept, TruthRecord(s.shipment_id, ANOMALY_MISROUTE, leg.arrival_h, leg.to_hub)
    return None


def _strand_lost_scan(rng, s, scans, now):
    # The parcel came off the truck but was never scanned in, so it sits unsorted.
    idx = [k for k, sc in enumerate(scans)
           if sc[2] == SCAN_UNLOADED and sc[0] < now - 1.0 and sc[1] != s.dest_hub]
    if not idx:
        return None
    k = rng.choice(idx)
    ts, hub = scans[k][0], scans[k][1]
    return scans[:k], TruthRecord(s.shipment_id, ANOMALY_LOST_SCAN, ts, hub)


def apply_strandings(rng: random.Random, cfg: ScenarioConfig, shipments: list[Shipment],
                     paths: dict[str, list[TripLeg]], legs: list[TripLeg], hubs: list[Hub],
                     ) -> tuple[dict[str, list[RawScan]], list[TruthRecord]]:
    now = cfg.now_h
    all_scans = {s.shipment_id: planned_scans(s, paths[s.shipment_id]) for s in shipments}
    by_id = {s.shipment_id: s for s in shipments}
    legs_from_hub: dict[str, list[TripLeg]] = {}
    for l in legs:
        legs_from_hub.setdefault(l.from_hub, []).append(l)

    # Split the anomaly budget evenly over the three individual types, and draw each
    # type only from shipments whose history so far allows it (e.g. a misroute needs
    # a load scan an hour or more before now). Types with too few candidates fall
    # back to HELD, which nearly every in-flight shipment can have.
    in_flight = [s for s in shipments if s.planned_arrival_h > now]
    n_anom = round(cfg.anomaly_rate * len(in_flight))
    kinds = [(ANOMALY_MISROUTE, ANOMALY_LOST_SCAN, ANOMALY_HELD)[i % 3] for i in range(n_anom)]
    rng.shuffle(kinds)
    pool = in_flight[:]
    rng.shuffle(pool)
    truth: list[TruthRecord] = []
    stranded: set[str] = set()

    def strand_one(kind: str) -> bool:
        for s in pool:
            if s.shipment_id in stranded:
                continue
            scans = all_scans[s.shipment_id]
            if kind == ANOMALY_MISROUTE:
                result = _strand_misroute(rng, s, scans, now, paths[s.shipment_id], legs_from_hub)
            elif kind == ANOMALY_LOST_SCAN:
                result = _strand_lost_scan(rng, s, scans, now)
            else:
                result = _strand_held(rng, s, scans, now)
            if result is not None:
                all_scans[s.shipment_id], rec = result
                truth.append(rec)
                stranded.add(s.shipment_id)
                return True
        return False

    for kind in kinds:
        strand_one(kind) or strand_one(ANOMALY_HELD)

    # MISSORT bursts: every parcel transferring through the hub in the window halts
    # there. Each burst is anchored on a real transfer unload so it is never empty.
    transfers = []  # (ts, hub) of unloads followed by a reload at the same hub
    for sid, scans in all_scans.items():
        if sid in stranded:
            continue
        for k, sc in enumerate(scans[:-1]):
            if sc[2] == SCAN_UNLOADED and scans[k + 1][2] == SCAN_LOADED and now - 14.0 <= sc[0] <= now - 3.0:
                transfers.append((sc[0], sc[1]))
    transfers.sort()
    used_hubs: set[str] = set()
    for n in range(1, cfg.n_incidents + 1):
        cands = [t for t in transfers if t[1] not in used_hubs]
        if not cands:
            break
        anchor_ts, hub = rng.choice(cands)
        used_hubs.add(hub)
        start = anchor_ts - rng.uniform(0.0, 1.5)
        end = start + rng.uniform(2.0, 5.0)
        inc_id = f"I{n:02d}"
        for sid, scans in all_scans.items():
            if sid in stranded:
                continue
            for k, sc in enumerate(scans):
                if (sc[2] == SCAN_UNLOADED and sc[1] == hub and start <= sc[0] <= end
                        and sc[1] != by_id[sid].dest_hub):
                    all_scans[sid] = scans[:k + 1]
                    truth.append(TruthRecord(sid, ANOMALY_MISSORT, sc[0], hub, inc_id))
                    stranded.add(sid)
                    break

    truth.sort(key=lambda t: t.shipment_id)
    return all_scans, truth


# --------------------------------------------------------------------------- disruptions

def build_disruptions(rng: random.Random, cfg: ScenarioConfig, hubs: list[Hub], lanes: list[Lane],
                      trips: list[Trip]) -> list[Disruption]:
    """Known events the recovery plan must route around. They start at or after `now`,
    so they don't rewrite the scan history the operator has already seen."""
    now = cfg.now_h
    out = []
    for n in range(1, cfg.n_disruptions + 1):
        kind = rng.choices((DISRUPTION_TRIP_BREAKDOWN, DISRUPTION_HUB_CLOSURE, DISRUPTION_LANE_DELAY),
                           weights=(0.4, 0.2, 0.4))[0]
        start = _r(rng.uniform(now, now + 8.0))
        did = f"D{n:02d}"
        if kind == DISRUPTION_TRIP_BREAKDOWN:
            # Breaks down mid-trip or before departure; the vehicle is out for the rest of the horizon.
            cands = [t for t in trips if t.departure_h <= start + 6.0 and t.arrival_h > start]
            trip = rng.choice(cands)
            out.append(Disruption(did, kind, start, cfg.horizon_h, trip_id=trip.trip_id))
        elif kind == DISRUPTION_HUB_CLOSURE:
            hub = rng.choice(hubs)
            out.append(Disruption(did, kind, start, _r(start + rng.uniform(4.0, 12.0)), hub_id=hub.hub_id))
        else:
            lane = rng.choice(lanes)
            out.append(Disruption(did, kind, start, _r(start + rng.uniform(6.0, 24.0)),
                                  lane_id=lane.lane_id, delay_h=_r(rng.uniform(2.0, 8.0))))
    return out


# --------------------------------------------------------------------------- entry point

def generate_scenario(seed: int = 42, **overrides) -> Scenario:
    cfg = replace(ScenarioConfig(), seed=seed, **overrides)
    if not 0.0 <= cfg.capacity_tightness <= 0.99:
        raise ValueError("capacity_tightness must be in [0, 0.99]")
    if not 0.0 <= cfg.anomaly_rate <= 1.0:
        raise ValueError("anomaly_rate must be in [0, 1]")
    if not 0.0 < cfg.now_h < cfg.horizon_h:
        raise ValueError("now_h must be inside the horizon")

    rng = random.Random(seed)
    hubs = list(HUBS)
    lanes = build_lanes(hubs)
    corridors = build_corridors(rng, hubs, lanes)
    trips, legs = build_trips(rng, cfg, corridors, lanes)
    shipments, paths = build_shipments(rng, cfg, hubs, Router(legs))
    raw, truth = apply_strandings(rng, cfg, shipments, paths, legs, hubs)
    disruptions = build_disruptions(rng, cfg, hubs, lanes, trips)

    scans: list[ScanEvent] = []
    for s in shipments:
        for ts, hub, kind, leg_id, note in raw[s.shipment_id]:
            if ts <= cfg.now_h:
                scans.append(ScanEvent(f"E{len(scans) + 1:06d}", s.shipment_id, ts, hub, kind, leg_id, note))

    return Scenario(cfg, hubs, lanes, corridors, trips, legs, shipments, scans, disruptions, truth)
