# Piggyback

A delay-aware shipment recovery engine. It finds stranded or misplaced shipments and recovers them by loading them onto trips that are already scheduled and have spare capacity. See `docs/DECISIONS.md` for the stack, scope and benchmark design.

**Status:** steps 1–5 of 7 are built: the schema, the seeded simulator, detection, recovery options, the optimizer and a replay benchmark. The API and the dashboard are not built yet.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                                        # 40 tests
python -m simulator.cli --seed 42 --out data/scenario_42   # CSVs + scenario.json + summary.json
python -m detection.cli data/scenario_42                   # detections.csv + score vs truth
python -m detection.cli data/scenario_42 --sweep           # precision/recall across grace periods
python -m recovery.cli data/scenario_42                    # plan_*.csv per solver + replayed KPIs
python scripts/eval_detection.py 30                        # detection over seeds 1-30
python scripts/eval_recovery.py 10                         # recovery over seeds 1-10 x 3 tightness levels
```

Database (needs Docker running):

```bash
docker compose up -d db                  # applies db/schema.sql on first start
scripts/load_db.sh data/scenario_42      # loads the CSVs, builds PostGIS geometry
```

On Windows, run `load_db.sh` from Git Bash.

## What the simulator builds

One call to `generate_scenario(seed=...)` returns a complete, replayable world (seed 42 numbers shown):

- **Network**: 25 Indian hub cities and 90 directed lanes (minimum spanning tree + 3 nearest neighbours, so always connected).
- **Timetable**: 80 corridors, each repeating every 12 h or 24 h. That gives 660 trips and 1,296 legs over 144 h, so recovery can book departures well after "now". Every leg has sampled spare kg and m³.
- **Shipments**: 3,000 with priority-based deadlines, each booked on its earliest-arrival itinerary. The operator sees only scans that reached the system by "now" (hour 36).
- **Noise**: 20% of trips run 0.5–4 h late, and 5% of scans upload 1–6 h late. Healthy parcels therefore sometimes look lost.
- **Strandings**: individual `MISROUTE`, `LOST_SCAN` and `HELD` cases, plus burst `MISSORT` incidents where many shipments miss their next leg at one hub. There are also `MISSED_CONNECTION`s caused by late trips. Seed 42 has 214 stranded shipments.
- **Disruptions**: trip breakdowns, hub closures and lane delays, starting at or up to 8 h after "now".
- **Ground truth** is kept apart (`sim_truth` and `sim_trip_delays` tables, `Scenario.truth`) so detection and planning never see it. It exists only for scoring and replay.

Benchmark knobs: `capacity_tightness`, `anomaly_rate`, `n_incidents`, `n_disruptions`, `n_shipments`, `trip_delay_rate`, `scan_lag_rate`. Each is a CLI flag too, e.g. `--capacity-tightness 0.8`.

## Detection

`detection.detect(view)` sees only an `OperatorView`: the timetable, shipments, visible scans and disruptions, never the truth. For each undelivered shipment it compares the visible scans with the scans its booking should have produced. Expected times are shifted by how late each trip is observed to be running. It flags `HELD`, `MISROUTE`, `LOST_SCAN`, `MISSED_CONNECTION`, `STALLED` and `MISSORT` (a cluster of stalls at one hub). It also flags `AT_RISK` for parcels on a late truck that will miss their connection. Each detection includes the hub where the parcel probably is, how overdue it is, a confidence score and the evidence.

Over seeds 1–30 with default settings: precision 0.918, recall 0.965, F1 0.941. Type and hub are right for over 99% of correct flags. Most false positives are scans that uploaded late. `--grace` trades recall for precision.

## Recovery

1. **Options** (`recovery.generate_options`): for each detection, the parcel is assumed to be at the suspected hub, ready after a handling time that depends on what went wrong. It gets up to 3 **piggyback** itineraries on scheduled trips with enough spare kg and m³, a **dedicated** truck, **local** delivery if it is already at its destination, or **none**. The planner's timetable shifts trips by their observed delay, applies lane delays, drops legs after breakdowns, and blocks loading and unloading at closed hubs.
2. **Optimizer** (`recovery.solve_cpsat`): picks one option per shipment to minimize cost + ₹250 × priority weight (EXPRESS 3, STANDARD 2, ECONOMY 1) × hours late. Every leg must stay within its spare capacity. It uses OR-Tools CP-SAT and proves the optimum in well under a second at default tightness. The baselines are `solve_greedy` (most to lose goes first) and `solve_none`.
3. **Replay** (`recovery.replay`): runs a plan against the truth. A plan fails if the parcel isn't at the hub the plan starts from, or if a trip ran later than anyone had observed and a connection breaks.

Totals over seeds 1–10, replayed against truth:

| `capacity_tightness` | Solver | Stranded on time | Unrecovered | Realized penalty |
|---|---|---|---|---|
| 0.3 | CP-SAT | 68.7% | 81 | ₹95.5 lakh |
| 0.3 | greedy | 68.5% | 83 | ₹96.5 lakh |
| 0.6 | CP-SAT | 69.2% | 133 | ₹122.9 lakh |
| 0.6 | greedy | 68.2% | 139 | ₹129.1 lakh |
| 0.9 | CP-SAT | 63.2% | 278 | ₹145.3 lakh |
| 0.9 | greedy | 60.9% | 347 | ₹164.4 lakh |
| any | do nothing | 0% | all | ₹570–650 lakh |

The tighter the spare capacity, the more the optimizer gains over greedy (12% lower penalty at 0.9).

## Layout

```
simulator/   models, hub data, generator, CSV/JSON export, CLI
detection/   operator view (no truth), detector, scoring, CLI
recovery/    planner's timetable, options, CP-SAT/greedy optimizer, replay vs truth, CLI
db/          schema.sql (PostgreSQL + PostGIS), load.sql
scripts/     load_db.sh, eval_detection.py, eval_recovery.py
tests/       simulator, schema/loader/CSV cross-checks, detection, recovery
docs/        DECISIONS.md
```

## Known limitations

- `db/schema.sql` and `scripts/load_db.sh` have been run against PostgreSQL 16 + PostGIS 3.4 (`postgis/postgis:16-3.4`) with seed 42. All constraints passed and reloading is idempotent. The pytest suite only cross-checks them statically; it does not start a database. The schema is applied only when the volume is first created, so after a schema change run `docker compose down -v` before `up`.
- Detection and recovery run in Python on CSVs or an in-memory scenario. They don't query Postgres yet, and plans are written to CSV, not to the database.
- The replay checks each parcel's own plan against the truth, but doesn't re-simulate the whole network afterwards. A recovered parcel doesn't change other parcels' outcomes.
- About 4% of hub pairs can't be completed before the timetable ends at hour 144 (long cross-country trips with several transfers). For those, recovery can only use a dedicated truck.
- Road times are haversine × 1.3 at 45 km/h until an OSRM matrix replaces them. Vehicle costs and the ₹250 value of a late hour are assumed figures. Spare capacity is sampled rather than derived from real loads. The calibration numbers in `docs/DECISIONS.md` describe these assumptions, not real-world carriers.
