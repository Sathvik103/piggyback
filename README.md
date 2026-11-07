# Piggyback

A delay-aware shipment recovery engine. It finds stranded or misplaced shipments and recovers them by loading them onto trips that are already scheduled and have spare capacity. See `docs/DECISIONS.md` for the stack, scope and benchmark design.

**Status:** step 1 of 7 (schema + seeded simulator). Detection, option generation, the optimizer and the dashboard are not built yet.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q                                        # 21 tests
python -m simulator.cli --seed 42 --out data/scenario_42   # CSVs + scenario.json + summary.json
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
- **Timetable**: 80 corridors, each repeating every 12 h or 24 h. That gives 330 trips and 648 legs over 72 h. Every leg has sampled spare kg and m³.
- **Shipments**: 3,000 with priority-based deadlines, each booked on its earliest-arrival itinerary. Scan histories are cut off at "now" (hour 36), which is all the operator can see.
- **Strandings**: individual `MISROUTE`, `LOST_SCAN` and `HELD` cases, plus burst `MISSORT` incidents where many shipments halt at one hub. Seed 42 has 235 stranded shipments.
- **Disruptions**: trip breakdowns, hub closures and lane delays, starting at or up to 8 h after "now".
- **Ground truth** is kept apart (`sim_truth` table, `Scenario.truth`) so detection code never sees it. It exists only to score detection.

Benchmark knobs: `capacity_tightness`, `anomaly_rate`, `n_incidents`, `n_disruptions`, `n_shipments`. Each is a CLI flag too, e.g. `--capacity-tightness 0.8`.

## Layout

```
simulator/   models, hub data, generator, CSV/JSON export, CLI
db/          schema.sql (PostgreSQL + PostGIS), load.sql
scripts/     load_db.sh
tests/       simulator invariants, determinism, schema/loader/CSV cross-checks
docs/        DECISIONS.md
```

## Known limitations

- `db/schema.sql` and `scripts/load_db.sh` have been run against PostgreSQL 16 + PostGIS 3.4 (`postgis/postgis:16-3.4`) with seed 42. All constraints passed and reloading is idempotent. The pytest suite only cross-checks them statically; it does not start a database.
- Road times are haversine × 1.3 at 45 km/h until an OSRM matrix replaces them. Vehicle costs are assumed INR/km figures. Spare capacity is sampled rather than derived from real loads. The calibration numbers in `docs/DECISIONS.md` describe these assumptions, not real-world carriers.
