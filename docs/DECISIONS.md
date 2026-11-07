# Decisions

Written at step 1 (2026-11-07). Steps 2–7 are proposals: revisit them before building each one.

## What Piggyback is

Piggyback finds shipments that are stranded or misplaced in a hub-and-spoke line-haul network. It recovers them by booking them onto trips that are already scheduled and have spare capacity, rather than sending a dedicated vehicle. It is delay-aware: the recovery plan is judged on how late each shipment lands against its deadline, weighted by priority, and on the extra cost.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Simulator, engine | Python 3.11+, standard library only | Deterministic with one `random.Random(seed)`; no NumPy version drift in benchmarks |
| Storage | PostgreSQL 16 + PostGIS 3.4 (`postgis/postgis:16-3.4`) | Relational timetable + geography for maps and distance queries |
| Optimizer (step 4) | OR-Tools CP-SAT, or a MIP via OR-Tools' linear solver | Assignment of shipments to legs with capacity is a small integer program |
| API + dashboard (steps 6–7) | FastAPI + a MapLibre front end | Proposed; nothing built yet |
| Tests | pytest | |

Times are stored as **hours since scenario start** (floats), not timestamps. This keeps the simulator, SQL and the optimizer in the same unit. The dashboard can map them to wall-clock time.

## Scope (7 steps)

1. **Schema + seeded simulator** (done): the world, scan histories, strandings, disruptions and ground truth.
2. **Detection**: flag stranded or misplaced shipments using only `scan_events`, the timetable and `disruptions`. Score precision and recall against `sim_truth`.
3. **Option generation**: for each flagged shipment, list feasible recovery itineraries over trip legs departing after `now`. Respect spare kg/m³, connection times and disruptions.
4. **Optimizer**: choose one option per shipment (or none) under shared leg capacity. Minimise priority-weighted lateness plus cost.
5. **Replay benchmark**: run 2–4 over a grid of seeds × knobs. Compare against baselines (do nothing, greedy earliest-arrival, dedicated vehicle).
6. **API**: serve scenarios, detections and plans.
7. **Dashboard**: map of hubs and lanes, stranded shipments, the proposed plan and its KPIs.

Out of scope: real carrier integrations, last-mile delivery, pricing, live OSRM routing (planned as a later swap for road times).

## Information boundary

Detection and recovery code may read: `hubs`, `lanes`, `corridors`, `trips`, `trip_legs`, `shipments`, `scan_events`, `disruptions`. It must never read `sim_truth` (or `Scenario.truth`). That table exists only to score step 2 and to measure recovery quality in step 5.

`scan_events` only holds scans with `ts_h <= now`. Planned future scans are not stored anywhere. They can be re-derived from `shipments.planned_legs`.

## Simulator model

- **Network**: 25 Indian hubs. Lanes are the minimum spanning tree plus each hub's 3 nearest neighbours, in both directions, so the graph is always connected (90 directed lanes with the current hub list).
- **Timetable**: corridors are 1–3-lane paths. First every lane gets one, then random extras up to 80 corridors. Each runs every 12 h or 24 h from a random offset. Trips depart within the 72 h horizon. There is 1 h of dwell at intermediate stops.
- **Spare capacity** per leg is a Beta draw. Its mean falls linearly from ~0.58 of vehicle capacity at `capacity_tightness=0` to ~0.03 at 1. It is sampled per leg, not derived from booked loads.
- **Shipments**: origin and destination are weighted by hub size and created uniformly in `[0, now-1)`. Each is booked on the earliest-arrival itinerary (connection scan). That itinerary has 1 h induction and 2 h minimum connection between trips; staying aboard needs none. Deadline = created + fixed + slack × planned transit: EXPRESS 2 h + 1.15×, STANDARD 4 h + 1.4×, ECONOMY 8 h + 1.8×. So every shipment can meet its deadline if nothing goes wrong.
- **Scans**: INDUCTED, LOADED at boarding, UNLOADED at alighting, DELIVERED 0.5 h after the final unload.
- **Individual strandings**: `round(anomaly_rate × in-flight shipments)`, split evenly over three types. Each type is drawn from shipments whose history allows it:
  - `MISROUTE`: loaded onto a wrong leg (to a hub not on its plan) instead of its booked one. Last scan is UNLOADED at that off-plan hub.
  - `LOST_SCAN`: came off the truck at a transfer hub but was never scanned in. Last visible scan is the LOADED; the parcel is physically at that leg's end hub.
  - `HELD`: HELD scan with a reason, never released.
- **MISSORT incidents** (`n_incidents`): a 2–5 h window at one hub, anchored on a real transfer 3–14 h before now. Every parcel unloaded there for transfer in the window halts.
- **Disruptions** (`n_disruptions`): trip breakdowns (40 %), hub closures (20 %) and lane delays (40 %). Each starts in `[now, now+8h]`. They constrain recovery and do not rewrite the scan history already seen.

## Benchmark design

A benchmark case is `(seed, capacity_tightness, anomaly_rate, n_incidents, n_disruptions, n_shipments)`. The same tuple always reproduces the same scenario (tested).

- **Detection metrics**: precision, recall and F1 against `sim_truth`. Also the hub-location accuracy (`true_hub_id`) and recall per anomaly type.
- **Recovery metrics**: share of stranded shipments recovered before their deadline, and priority-weighted hours late (EXPRESS 3, STANDARD 2, ECONOMY 1). Also incremental cost in INR, capacity used on piggybacked legs, and solve time.
- **Default grid**: seeds 1–20 × `capacity_tightness ∈ {0.2, 0.5, 0.8}` × `anomaly_rate ∈ {0.02, 0.04, 0.08}`, all with `n_incidents=2` and `n_disruptions=8`.

## Calibration assumptions (not real-world data)

- Road distance = great-circle × 1.3; average line-haul speed 45 km/h including stops. To be replaced by an OSRM matrix.
- Vehicles: 14 ft (2.5 t, 18 m³, ₹22/km), 20 ft (5 t, 32 m³, ₹32/km), 32 ft MXL (9 t, 60 m³, ₹48/km). These are assumed figures, not carrier quotes.
- Parcel weight is log-normal (median 8 kg, clipped to 0.2–400 kg); density is 120–300 kg/m³.
- Hub sort capacities are illustrative; nothing uses them yet.
