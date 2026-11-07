# Decisions

Written at step 1 (2026-11-07). Steps 2–7 are proposals: revisit them before building each one.

## What Piggyback is

Piggyback finds shipments that are stranded or misplaced in a hub-and-spoke line-haul network. It recovers them by booking them onto trips that are already scheduled and have spare capacity, rather than sending a dedicated vehicle. It is delay-aware: the recovery plan is judged on how late each shipment lands against its deadline, weighted by priority, and on the extra cost.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Simulator, engine | Python 3.11+, standard library only | Deterministic with one `random.Random(seed)`; no NumPy version drift in benchmarks |
| Storage | PostgreSQL 16 + PostGIS 3.4 via psycopg 3 + psycopg-pool; numbered SQL migrations | System of record for scenarios, plans and decisions. Scenarios live side by side (keyed by `scenario_id`); capacity holds are a view over the decision log |
| Optimizer (step 4) | OR-Tools CP-SAT (`ortools>=9.11,<10`) | Choosing options under shared leg capacity is a small integer program; CP-SAT proves optimality in under a second at default size |
| API (step 6) | FastAPI + uvicorn, over scenario folders | Built. Typed request validation and free OpenAPI docs; file-backed so it runs without Docker |
| Dashboard (step 7) | Plain HTML/CSS/JS + MapLibre GL 4.7.1, served by the API | Built. No build step or framework for one page; MapLibre for a GPU map with GeoJSON layers |
| Tests | pytest | |

Times are stored as **hours since scenario start** (floats), not timestamps. This keeps the simulator, SQL and the optimizer in the same unit. The dashboard can map them to wall-clock time.

## Scope (7 steps)

1. **Schema + seeded simulator** (done): the world, scan histories, strandings, disruptions and ground truth.
2. **Detection** (done): flag stranded or misplaced shipments using only `scan_events`, the timetable and `disruptions`. Score precision and recall against `sim_truth`. See "Detection" below.
3. **Option generation** (done): for each flagged shipment, list feasible recovery itineraries over trip legs departing after `now`. Respect spare kg/m³, connection times and disruptions.
4. **Optimizer** (done): choose one option per shipment (or none) under shared leg capacity. Minimise priority-weighted lateness plus cost.
5. **Replay benchmark** (done, first version): run 2–4 over seeds × capacity tightness. Compare against greedy and do-nothing baselines. See "Recovery" below.
6. **API** (done): serve scenarios, detections and plans. Ground truth only under `/evaluation/` routes (see README).
7. **Dashboard** (done): map of hubs and lanes, stranded shipments, the proposed plan and its KPIs. Truth-based numbers are opt-in and visually separated (see README).

Out of scope: real carrier integrations, last-mile delivery, pricing, live OSRM routing (planned as a later swap for road times).

## Information boundary

Detection and recovery code may read: `hubs`, `lanes`, `corridors`, `trips`, `trip_legs`, `shipments`, `scan_events`, `disruptions`. It must never read the `sim_*` tables (`sim_truth`, `sim_trip_delays`) or `Scenario.truth` / `Scenario.trip_delays`. Those exist only to score step 2 and to replay recovery in step 5. In code, detection reads through `detection.OperatorView`, which has no truth fields. Its CSV loader skips the `sim_*` files, and a test checks this. In Postgres the same split holds: the repository's `view()` never reads `sim_*`, and only the API's `/evaluation/` routes call `truth()`.

**Clock time.** Each scenario has a `start_at` (default 2026-11-02 00:00 IST). Every `*_h` field is hours after it; the API's summary also gives `now_at`.

`scan_events` only holds scans with `recorded_h <= now`. `ts_h` is when the scan happened; `recorded_h` is when it reached the system. Planned future scans are not stored anywhere. They can be re-derived from `shipments.planned_legs`.

## Simulator model

- **Network**: 25 Indian hubs. Lanes are the minimum spanning tree plus each hub's 3 nearest neighbours, in both directions, so the graph is always connected (90 directed lanes with the current hub list).
- **Timetable**: corridors are 1–3-lane paths. First every lane gets one, then random extras up to 80 corridors. Each runs every 12 h or 24 h from a random offset. Trips depart within `horizon_h` (144 h, so recovery at `now` = 36 h has days of departures to book onto; with 72 h, about a quarter of stranded parcels had no scheduled way home). There is 1 h of dwell at intermediate stops.
- **Spare capacity** per leg is a Beta draw. Its mean falls linearly from ~0.58 of vehicle capacity at `capacity_tightness=0` to ~0.03 at 1. It is sampled per leg, not derived from booked loads.
- **Shipments**: origin and destination are weighted by hub size and created uniformly in `[0, now-1)`. Each is booked on the earliest-arrival itinerary (connection scan). That itinerary has 1 h induction and 2 h minimum connection between trips; staying aboard needs none. Deadline = created + fixed + slack × planned transit: EXPRESS 2 h + 1.15×, STANDARD 4 h + 1.4×, ECONOMY 8 h + 1.8×. So every shipment can meet its deadline if nothing goes wrong.
- **Scans**: INDUCTED, LOADED at boarding, UNLOADED at alighting, DELIVERED 0.5 h after the final unload.
- **Trip delays** (`trip_delay_rate`, default 0.2): a late trip is shifted by 0.5–4 h end to end. The actual delays are truth (`sim_trip_delays`). The operator can only infer them from the scans of parcels on board.
- **Upload lag** (`scan_lag_rate`, default 0.05): that share of scans reaches the system 1–6 h after it happened; all others arrive within 15 min. Lag hides scans and never changes `ts_h`.
- **`MISSED_CONNECTION`**: a late trip arrives at a transfer hub less than the 2 h minimum connection before the next booked leg departs (that leg's own delay counts). The parcel is stuck at that hub. It goes into truth only if the arrival is before `now`.
- **Individual strandings**: `round(anomaly_rate × in-flight shipments)`, split evenly over three types. Each type is drawn from shipments whose history allows it:
  - `MISROUTE`: loaded onto a wrong leg (to a hub not on its plan) instead of its booked one. Last scan is UNLOADED at that off-plan hub.
  - `LOST_SCAN`: came off the truck at a transfer hub but was never scanned in. Last visible scan is the LOADED; the parcel is physically at that leg's end hub.
  - `HELD`: HELD scan with a reason, never released.
- **MISSORT incidents** (`n_incidents`): a 2–5 h window at one hub, anchored on a real transfer 3–14 h before now. A parcel unloaded there for transfer in the window is stranded only if its next leg has already departed by `now`. Parcels whose leg leaves later can still be found in time, and at `now` they look exactly like parcels waiting normally, so they stay healthy.
- **Disruptions** (`n_disruptions`): trip breakdowns (40 %), hub closures (20 %) and lane delays (40 %). Each starts in `[now, now+8h]`. They constrain recovery and do not rewrite the scan history already seen.

## Detection

The detector is rule-based and explainable on purpose. It sets the baseline later learned detectors must beat, and an operator can read each flag's evidence. It works per undelivered shipment, comparing visible scans with the scans the booking should have produced. Expected times are shifted by each trip's **observed** delay, the largest gap between any parcel's scan on that trip and the timetable.

| Kind | Rule | Suspected hub |
|---|---|---|
| `HELD` | latest scan is HELD | that hub |
| `MISROUTE` | any scan on a leg outside the booking | the wrong leg's end hub |
| `MISSED_CONNECTION` | current or last ride arrives (observed or predicted) too late for the next booked leg, and that arrival is in the past | transfer hub |
| `AT_RISK` | the same, arrival still ahead | transfer hub; not a stranding, scoring ignores it |
| `LOST_SCAN` | expected unload more than `grace_h` (2 h) overdue | the leg's end hub |
| `STALLED` | expected load more than `grace_h` overdue, on a trip someone was seen on (or more than `unobserved_trip_wait_h`, 4 h, overdue) | current hub |
| `MISSORT` | at least `cluster_min` (3) stalls at one hub, each within 3 h of the last; flagged once each is `burst_grace_h` (0.5 h) overdue | that hub |

Why the two waits: a single missing scan is often just uploading late, so it waits `grace_h`. A pile-up at one hub isn't explained by independent upload lag, so it can be flagged sooner. A trip nobody has scanned on may just be late, so a missing load on it waits longer.

Results over seeds 1–30 (defaults): precision 0.918, recall 0.965, F1 0.941. Recall by type: MISROUTE 100%, MISSED_CONNECTION 100%, HELD 99%, LOST_SCAN 93%, MISSORT 92%. False positives are mostly `STALLED` (269) and `LOST_SCAN` (150), both from late uploads. Raising `grace_h` to 4–6 h removes most of them at the cost of recall (`python -m detection.cli <dir> --sweep`).

## Recovery

**Where the parcel starts.** At the detector's suspected hub. It is ready at `now` plus a handling time that depends on the detection:

| Detection | Handling time |
|---|---|
| HELD | 2 h |
| LOST_SCAN | 1.5 h |
| MISSORT, STALLED | 1 h |
| MISROUTE, MISSED_CONNECTION | 0.5 h |

An `AT_RISK` parcel is ready at its predicted arrival plus the 2 h minimum connection, so it can be re-booked before it is actually stranded.

**Options.** Each shipment gets:
- up to 3 **piggyback** itineraries (alternatives come from banning the previous option's first trip),
- a **dedicated** truck: the smallest vehicle that fits, 1.5 h to dispatch, then road time direct,
- **local** delivery if it is already at its destination,
- **none**.

A piggyback itinerary may only use legs with enough spare kg and m³. The exception is the shipment's own booked legs, where its space is already reserved; those don't count against spare capacity.

**Costs (assumed).**
- **Piggyback:** ₹60 per hand-off onto a trip, plus ₹0.004 per kg·km on legs outside its booking.
- **Dedicated:** the vehicle's ₹/km × road km.
- **None:** charged as 48 priority-weighted hours late.

Penalty = cost + ₹250 × priority weight (EXPRESS 3, STANDARD 2, ECONOMY 1) × hours late.

**Planner's timetable.** Trips are shifted by their observed delay. A known lane delay adds to the trip from that leg on. Legs after a breakdown are dropped. A closed hub blocks loading and unloading, but not trucks passing through.

**Optimizer.** It picks exactly one option per shipment, subject to Σ kg ≤ spare kg and Σ m³ ≤ spare m³ on every leg. CP-SAT works in integers (kg × 10, m³ × 1000, whole rupees), and only legs that could actually overflow get a constraint. The baselines are greedy (shipments in order of their worst penalty, each taking its cheapest option that still fits) and do nothing.

**Replay** (`recovery.replay`). Uses the truth: the real hub (`sim_truth`) and real trip delays (`sim_trip_delays`). Outcomes:

| Outcome | When |
|---|---|
| `ON_TIME` / `LATE` | the plan worked; by deadline or after it |
| `FAILED` | wrong hub, a leg never ran, or a connection broke because a trip ran later than observed |
| `UNRECOVERED` | not detected, or left |
| `UNNECESSARY` | acted on a healthy parcel (its cost counts) |

`AT_RISK` parcels count as stranded only if they really miss their connection under the true delays. Failed and unrecovered parcels are charged 48 h late.

**Results** (seeds 1–10). CP-SAT and greedy both get about 61–69% of stranded parcels home on time, against 0% for doing nothing, and cut the realized penalty by 4–6×. CP-SAT's realized penalty is 1% lower than greedy's at `capacity_tightness` 0.3, 5% lower at 0.6, and 12% lower at 0.9, where it also leaves 20% fewer parcels unrecovered. About 5% of plans fail in replay. The main causes are trips running later than observed, which breaks planned connections, and the occasional wrong hub from detection. A connection buffer for trips nobody has been seen on is the obvious next improvement.

## Benchmark design

A benchmark case is `(seed, capacity_tightness, anomaly_rate, n_incidents, n_disruptions, n_shipments)`. The same tuple always reproduces the same scenario (tested).

- **Detection metrics**: precision, recall and F1 against `sim_truth`. Also the hub-location accuracy (`true_hub_id`) and recall per anomaly type.
- **Recovery metrics**: share of stranded shipments recovered before their deadline, and priority-weighted hours late (EXPRESS 3, STANDARD 2, ECONOMY 1). Also incremental cost in INR, capacity used on piggybacked legs, and solve time.
- **Default grid**: seeds 1–20 × `capacity_tightness ∈ {0.2, 0.5, 0.8}` × `anomaly_rate ∈ {0.02, 0.04, 0.08}`, all with `n_incidents=2` and `n_disruptions=8`. `scripts/eval_recovery.py` currently runs a smaller grid (seeds 1–N × tightness 0.3/0.6/0.9).

## Calibration assumptions (not real-world data)

- Road distance = great-circle × 1.3; average line-haul speed 45 km/h including stops. To be replaced by an OSRM matrix.
- Vehicles: 14 ft (2.5 t, 18 m³, ₹22/km), 20 ft (5 t, 32 m³, ₹32/km), 32 ft MXL (9 t, 60 m³, ₹48/km). These are assumed figures, not carrier quotes.
- Parcel weight is log-normal (median 8 kg, clipped to 0.2–400 kg); density is 120–300 kg/m³.
- Hub sort capacities are illustrative; nothing uses them yet.
