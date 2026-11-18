# <img src="docs/img/logo.svg" width="36" align="top" alt=""> Piggyback

[![CI](https://github.com/Sathvik103/piggyback/actions/workflows/ci.yml/badge.svg)](https://github.com/Sathvik103/piggyback/actions/workflows/ci.yml)

**Delay-aware recovery of stranded shipments onto trips that are already scheduled and have spare room.**

When a parcel gets stuck in a line-haul network (held, mis-sorted, misrouted, or stranded by a late truck), carriers typically rebook it onto the next scheduled service, or send a dedicated truck. Piggyback works differently:
- it detects stranded parcels from scan histories,
- it plans their recovery across all of them at once with a CP-SAT optimizer, on trucks that are already running with spare room,
- it plans on how late trucks are actually running, not on the published timetable,
- it prices each option by its expected outcome: how likely its connections are to hold, given how late trucks have been running, and how likely the flag is to be real,
- and it hands an operator a queue of explainable recommendations to accept, change or dismiss.

**Result.** Over 180 paired simulated scenarios against TMS-style next-service rebooking:
- **−31.1% realized penalty** (money spent plus value of hours late; 95% CI −28.9% to −33.4%), better in 177 of 180
- **+6.4 pp** stranded parcels on time
- **failed recoveries 2.8% vs 10.6%**

The win holds in all six stress conditions it wasn't designed around (−21% to −39%). Over whole 24-hour shifts, re-planned every hour as bookings break and new parcels strand, the margin is smaller but still there: **−10.8%** (±9.0), better in 13 of 20 shifts ([shift benchmark](#a-whole-shift-hour-by-hour)). Up to 20,000 shipments, plans come within 1.5% of the proven optimum. Road distances and drive times between hubs are real (OpenStreetMap via OSRM). [Full results](results/REPORT.md) · [robustness](results/ROBUSTNESS.md) · [whole shifts](results/SHIFT.md) · [scaling](results/SCALING.md) · [how it's measured](#benchmark-piggyback-vs-what-carriers-do-today)

![Demo: working the queue with the keyboard, comparing a recovery option, accepting it, then the network map and the benchmark](docs/img/demo.gif)

![Operator queue: urgency-sorted stranded parcels, a recovery card comparing options with arrival vs deadline, cost and truck load, and the route on a map](docs/img/queue.png)

<table><tr>
<td><img src="docs/img/network.png" alt="Network map with the plan's piggyback routes, dedicated trucks, closures and a hub's departures with free capacity"></td>
<td><img src="docs/img/benchmark.png" alt="Benchmark page: headline result with confidence interval and the stress-test dot plot"></td>
</tr></table>

![Shift replay: a whole simulated shift worked hour by hour by Piggyback and next-service rebooking, with a time slider, recoveries in flight on the map, and both policies' results side by side](docs/img/replay.png)

## How it works

```mermaid
flowchart LR
    SIM["Simulator<br/>(or real scan feed)"] -->|timetable, shipments,<br/>scans, disruptions| DB[(Postgres + PostGIS)]
    DB --> DET["Detection<br/>scans vs booking,<br/>observed trip delays"]
    DET --> OPT["Options<br/>piggyback · truck · local<br/>on the delay-aware network"]
    OPT --> CPSAT["CP-SAT<br/>one option per parcel,<br/>shared truck capacity"]
    CPSAT --> UI["Operator console<br/>accept · alternative · dismiss"]
    UI -->|decisions hold capacity| DB
    SIM -.->|ground truth,<br/>scoring only| BENCH["Benchmark<br/>replay vs truth"]
    CPSAT --> BENCH
```

| Layer | What it is |
|---|---|
| `simulator/` | Seeded India line-haul world: 25 hubs, timetable, 3,000 shipments, scan histories cut at "now", strandings, disruptions, trip delays, upload lag, plus stress conditions. Ground truth kept apart. |
| `detection/` | Rule-based, explainable detector reading only what an operator can see. Precision 0.92 / recall 0.96 over 30 seeds. |
| `recovery/` | Delay- and disruption-aware planning network, recovery options priced by expected penalty, CP-SAT optimizer with greedy and do-nothing baselines, replay against the truth. |
| `benchmark/` | Head-to-head vs carrier-style policies, plus ablations, paired 95% CIs, a sensitivity analysis, stress tests, and whole shifts worked hour by hour. |
| `db/`, `api/` | Postgres as system of record (migrations, append-only decisions, capacity holds that can't be overbooked), FastAPI. |
| `web/` | React + TypeScript operator console (Vite, Tailwind, shadcn/ui, TanStack Query, MapLibre + deck.gl). |

**Status:** all 7 planned steps are built, with CI (lint, tests against PostGIS, Playwright end-to-end and axe accessibility tests, benchmark smoke run) and Docker. See [`docs/DECISIONS.md`](docs/DECISIONS.md) for the design decisions and their trade-offs.

## Quick start

```bash
pip install -r requirements.txt
python -m pytest                                           # all tests (DB tests skip if Postgres is down)
ruff check .                                               # lint
python -m simulator.cli --seed 42 --out data/scenario_42   # CSVs + scenario.json + summary.json
python -m detection.cli data/scenario_42                   # detections.csv + score vs truth
python -m detection.cli data/scenario_42 --sweep           # precision/recall across grace periods
python -m recovery.cli data/scenario_42                    # plan_*.csv per solver + replayed KPIs
python scripts/eval_detection.py 30                        # detection over seeds 1-30
python -m benchmark.run                                    # head-to-head benchmark, 180 scenarios (~13 min) -> results/
python -m benchmark.run --stress                           # same contest under harder conditions -> results/ROBUSTNESS.md
python -m benchmark.shift                                  # whole 24 h shifts, re-planned hourly -> results/SHIFT.md
(cd web && npm ci && npm run build)                        # build the web app into api/static
(cd web && npm run e2e)                                    # Playwright end-to-end tests against the running API (creates its own test users)
(cd web && npm run screenshots)                            # re-capture the README screenshots into docs/img
(cd web && npm run demo) && python scripts/make_demo_gif.py # re-record docs/img/demo.gif
python -m benchmark.scaling                                # plan time and optimality from 3k to 40k shipments
python scripts/loadtest.py --out results/LOAD.md          # many operators at once against the running API
python -m uvicorn api.app:create_app --factory --port 8000 # web app at http://localhost:8000/, API docs at /docs
```

Database (needs Docker running; the API needs it, the CLIs above don't):

```bash
docker compose up -d db                      # Postgres 16 + PostGIS on localhost:5433
docker compose --profile app up -d --build    # ...plus the API + web app container on :8000
python -m db migrate                         # apply db/migrations (the API also does this on start)
python -m db load data/scenario_42           # load a simulator.cli folder, or:
python -m db seed scenario_7 --seed 7        # generate straight into Postgres
python -m db user add asha --name "Asha R."  # an operator account (prompts for a password)
python -m db user add sam --role viewer      # read-only
```

The web app and every API route except `/health`, `/benchmark` and `/auth/*` need a signed-in user, so create one before opening http://localhost:8000/. With the Docker app profile, run it inside the container: `docker compose exec api python -m db user add asha`.

On Windows, `scripts/start_docker.ps1` starts Docker Desktop and the database. It works around a Docker Desktop bug where leftover socket files make it crash on start. The database is published on port **5433**, so it doesn't clash with a locally installed Postgres on 5432. Set `DATABASE_URL` to point elsewhere; tests use a separate `piggyback_test` database (`PIGGYBACK_TEST_DATABASE_URL`) that they wipe and re-migrate.

## What the simulator builds

One call to `generate_scenario(seed=...)` returns a complete, replayable world (seed 42 numbers shown):

- **Network**: 25 Indian hub cities and 94 directed lanes (minimum spanning tree + 3 nearest neighbours by real road distance, so always connected).
- **Timetable**: 80 corridors, each repeating every 12 h or 24 h. That gives 647 trips and 1,264 legs over 144 h, so recovery can book departures well after "now". Every leg has sampled spare kg and m³.
- **Shipments**: 3,000 with priority-based deadlines, each booked on its earliest-arrival itinerary. The operator sees only scans that reached the system by "now" (hour 36).
- **Noise**: 20% of trips run 0.5–4 h late, and 5% of scans upload 1–6 h late. Healthy parcels therefore sometimes look lost.
- **Strandings**: individual `MISROUTE`, `LOST_SCAN` and `HELD` cases, plus burst `MISSORT` incidents where many shipments miss their next leg at one hub. There are also `MISSED_CONNECTION`s caused by late trips. Seed 42 has 261 stranded shipments.
- **Disruptions**: trip breakdowns, hub closures and lane delays, starting at or up to 8 h after "now".
- **Ground truth** is kept apart (`sim_truth` and `sim_trip_delays` tables, `Scenario.truth`) so detection and planning never see it. It exists only for scoring and replay.

Benchmark knobs: `capacity_tightness`, `anomaly_rate`, `n_incidents`, `n_disruptions`, `n_shipments`, `trip_delay_rate`, `scan_lag_rate`. Each is a CLI flag too, e.g. `--capacity-tightness 0.8`.

## Detection

`detection.detect(view)` sees only an `OperatorView`: the timetable, shipments, visible scans and disruptions, never the truth. For each undelivered shipment it compares the visible scans with the scans its booking should have produced. Expected times are shifted by how late each trip is observed to be running. It flags `HELD`, `MISROUTE`, `LOST_SCAN`, `MISSED_CONNECTION`, `STALLED` and `MISSORT` (a cluster of stalls at one hub). It also flags `AT_RISK` for parcels on a late truck that will miss their connection. Each detection includes the hub where the parcel probably is, how overdue it is, a confidence score and the evidence.

Over seeds 1–30 with default settings: precision 0.918, recall 0.965, F1 0.941. Type and hub are right for over 99% of correct flags. Most false positives are scans that uploaded late. `--grace` trades recall for precision.

## Recovery

1. **Options** (`recovery.generate_options`): for each detection, the parcel is assumed to be at the suspected hub, ready after a handling time that depends on what went wrong. It gets up to 3 **piggyback** itineraries on scheduled trips with enough spare kg and m³, a **dedicated** truck, **local** delivery if it is already at its destination, or **none**. The planner's timetable shifts trips by their observed delay, applies lane delays, drops legs after breakdowns, and blocks loading and unloading at closed hubs.
2. **Risk pricing** (`recovery.risk`): a plan that rides several trucks depends on trucks that haven't run yet. Piggyback learns how late trucks run from the ones the operator has already watched (never from the truth), and prices each option by its *expected* penalty: the chance every transfer holds, the expected lateness if it does, and a rebooking penalty if it doesn't. Lateness is weighted by the calibrated chance that the flag is a real stranding (measured on validation seeds), so a costly truck for a doubtful flag rarely wins. Routes are generated with and without the old fixed transfer buffer and priced side by side, so a connection with an hour of slack that holds 85% of the time can beat a route that is certainly seven hours late.
3. **Optimizer** (`recovery.solve_cpsat`): picks one option per shipment to minimize cost + ₹250 × priority weight (EXPRESS 3, STANDARD 2, ECONOMY 1) × expected hours late. Every leg must stay within its spare capacity. It uses OR-Tools CP-SAT and proves the optimum in well under a second at default tightness. The baselines are `solve_greedy` (most to lose goes first) and `solve_none`.
4. **Replay** (`recovery.replay`): runs a plan against the truth. A plan fails if the parcel isn't at the hub the plan starts from, or if a trip ran later than anyone had observed and a connection breaks.

## Benchmark: Piggyback vs what carriers do today

`python -m benchmark.run` runs 180 simulated scenarios: seeds 1–20 × capacity tightness 0.3/0.6/0.9 × anomaly rate 0.02/0.04/0.08. On each one it runs every recovery policy below, replays every plan against the ground truth, and writes [results/REPORT.md](results/REPORT.md) with paired comparisons and 95% confidence intervals. All policies get the same detections, so the comparison isolates *recovery*.

**Baselines:**
- **Next service, FCFS:** a typical TMS auto-rebook. It books the earliest service with room on the published timetable, in the order problems appear.
- **Next service + truck for late EXPRESS:** the same, plus a control-tower rule of thumb.
- **Do nothing.**

Both next-service baselines are reactive, but they still rebook a parcel when it strands later.

**Results**, Piggyback minus baseline on the same scenarios (± is a 95% confidence interval):

| Piggyback vs | Δ stranded on time | Δ realized penalty¹ | Failed recoveries | Lower penalty in |
|---|---|---|---|---|
| Next service, FCFS | **+6.4 pp** (±0.7) | **−31.1%** (±2.2) | **2.8% vs 10.6%** | 177 / 180 |
| Next service + truck for late EXPRESS | −2.5 pp (±0.8) | **−48.4%** (±1.6) | 2.8% vs 9.6% | 180 / 180 |
| Do nothing | +69.8 pp | −85% | — | 180 / 180 |

¹ Money spent + ₹250 × priority weight × hours late. Against next-service, the win holds whatever a late hour is worth, from ₹50 to ₹1000 (−16.6% to −34.5%). The truck rule gets more parcels on time by spending ₹8.2 lakh per scenario on trucks, about 8.6× Piggyback's spend.

**Where the gain comes from** (ablations, each changing one ingredient):
- **Delay-aware planning is most of it.** Planning on observed trip delays and known disruptions, instead of the published timetable, is worth −25.5% penalty and cuts failed recoveries from 9.3% to 2.8%.
- **Risk pricing adds −5.0%** (±1.4, better in 135/180) over the previous default, which demanded a fixed 1.5 h of slack after any truck nobody had scanned yet. It gets +1.8 pp more parcels on time and leaves fewer unrecovered (3.8% vs 4.6%) by taking connections the fixed rule threw away. The price, disclosed: some of those gambles lose, so failed recoveries rise from 2.0% to 2.8%. Over whole shifts the gain is larger, −15.2% (±5.7, 17 of 20 shifts).
- **The CP-SAT optimizer adds −3.9% overall.** That's about zero when trucks have room, and −8.5% when capacity is tight (0.9). It matters most at scale (see below).
- **Stranding calibration barely matters here** (−0.2% ± 0.1). It weights lateness by the chance a flag is real, so trucks aren't sent to doubtful flags; with 92% detection precision there are few doubtful flags to save money on.
- **Acting early on at-risk parcels doesn't pay on the main grid.** Piggyback watches them and rebooks only once they really strand. Rebooking them early instead is no better (+0.6% ± 1.2, not significant). In two stress conditions (the default world's 20-scenario subset and noisy scans) early rebooking *was* significantly better, by about 5%, so this choice is close.

**Disclosure:** Piggyback originally rebooked at-risk parcels proactively. The benchmark's own ablation showed that didn't pay, so the default was switched to wait-then-act and the benchmark re-run. The same happened with risk pricing: it was built as a candidate, tuned only on validation seeds 101–110, and made the default only after it beat the fixed buffer on the reported seeds and over whole shifts. The numbers above are for the shipped default. Proactive rebooking is still available as `include_at_risk` on the API and `--include-at-risk` on the CLI.

**Robustness.** `python -m benchmark.run --stress` re-runs the contest under conditions Piggyback was not designed around, each changing one thing from the default world. Full tables are in [results/ROBUSTNESS.md](results/ROBUSTNESS.md).

| Condition (10 seeds × tightness 0.6/0.9) | Δ penalty vs next service | Δ on time | Failed: Piggyback / next service | Wins |
|---|---|---|---|---|
| Default world | **−27.3%** (±6.1) | **+5.3 pp** | 2.8% / 9.4% | 20 / 20 |
| Delays start mid-trip (early scans look on time) | **−25.6%** (±8.6) | **+4.4 pp** | 2.3% / 8.2% | 19 / 20 |
| 3% of trips silently cancelled | **−20.8%** (±4.9) | **+5.8 pp** | 8.3% / 15.5% | 20 / 20 |
| Spare room scarce on busy corridors and evenings | **−28.5%** (±5.8) | **+7.7 pp** | 3.2% / 10.9% | 20 / 20 |
| 3× noisier scan uploads | **−27.9%** (±6.0) | **+5.5 pp** | 2.8% / 9.3% | 20 / 20 |
| Twice as many late trips | **−39.1%** (±6.9) | **+8.7 pp** | 4.5% / 15.3% | 20 / 20 |

- **The win holds in every condition**, with every on-time gain significant. Delays that start mid-trip used to be the weak spot (−17.0% under the fixed buffer); risk pricing is worth −9.5% there, because the learned spread of delays already allows for trucks that look on time and then slip.
- **Silent cancellations** triple Piggyback's failures, since no planner can see them coming, but it still fails at about half the baseline's rate.

### A whole shift, hour by hour

The benchmark above is a snapshot: one look at the network, one plan. `python -m benchmark.shift` runs the network as a digital twin over a 24-hour shift instead ([results/SHIFT.md](results/SHIFT.md)). Every hour each policy sees only what has been scanned so far, books recoveries that hold real truck space for the rest of the shift, and finds out the hard way when a booking breaks: the parcel comes back 2 h later at the hub where it got stuck, for up to 3 tries. Bookings that break after the shift ends are still replanned, so nothing is scored as lost just because the clock stopped. 10 seeds × tightness 0.6/0.9:

| Piggyback vs | Δ on time | Δ realized penalty | Bookings that broke, per shift | Lower penalty in |
|---|---|---|---|---|
| Next service, FCFS | **+2.2 pp** (±1.4) | **−10.8%** (±9.0) | 23 vs 52 | 13 / 20 |
| Next service + truck for late EXPRESS | −6.9 pp (±1.2) | **−63.9%** (±5.3) | 23 vs 49 | 20 / 20 |
| Previous default (fixed 1.5 h buffer) | **+1.7 pp** (±0.8) | **−15.2%** (±5.7) | 23 vs 23 | 17 / 20 |

- **Next-service rebooking is a much tougher rival when it gets to try again every hour.** Its bookings break more than twice as often as Piggyback's, but each break is cheap to retry on the next truck, so the snapshot's −31% shrinks to about −11%, and Piggyback loses 7 of the 20 shifts.
- **The fixed buffer lost to next-service over whole shifts** (+7.1% penalty, ±14.0, better in only 10 of 20). Measuring why led to risk pricing: the buffer discarded connections that usually work, and hourly re-planning made those losses add up.

**Scaling.** `python -m benchmark.scaling` grows the network from 3,000 to 40,000 shipments with nearly full trucks (3 seeds each; full table in [results/SCALING.md](results/SCALING.md)). Plan time is what an operator waits for after pressing re-plan.

| Shipments | Stranded parcels | Plan time | CP-SAT proved optimal | Worst gap to optimum | Greedy's extra cost |
|---|---|---|---|---|---|
| 3,000 | 230 | 1.5 s | 3 / 3 | 0% | +17.3% |
| 6,000 | 580 | 33 s | 0 / 3 | 0.7% | +57.5% |
| 10,000 | 849 | 34 s | 0 / 3 | 1.5% | +55.8% |
| 20,000 | 1,678 | 37 s | 0 / 3 | 0.9% | +52.1% |
| 40,000 | 3,388 | 44 s | 0 / 3 | 17.1% | +39.8% |

Detection and option generation stay under 12 s even at 40,000 shipments. From 6,000 up, CP-SAT uses its 30 s limit, and up to 20,000 its lower bound proves the plan is within 1.5% of the best possible. Greedy, by contrast, falls 17–58% behind as contention grows.

**A cost of risk pricing, disclosed:** it prices routes with and without the transfer buffer, so there are about 15% more options to choose from, and at 40,000 shipments 30 s is no longer always enough. On two seeds the plan is within 1.4% of optimal; on the third it is 17% off. Given 90 s, that seed's plan is within 0.6%. Under the fixed buffer, the worst 40,000-shipment gap was 1.2%. Warm-starting CP-SAT from the greedy plan was tried earlier and dropped (it made plans 11.5% worse at 40,000). A greedy safety net means CP-SAT never returns anything worse than greedy.

These are results on Piggyback's own simulator. The benchmark is built to be fair: baselines get a second chance at parcels that strand later, and every number is reproducible from its seed. It is not evidence from a real network.

## API

`api/` is a FastAPI app backed by Postgres, which is the system of record for scenarios, plans and operator decisions. Expensive derived data (the operator view, the planner's network, detections per grace period) is cached in memory per scenario. Interactive docs are at `/docs`.

| Route | What it returns |
|---|---|
| `GET /scenarios`, `POST /scenarios` | List scenarios, or generate one from a seed and the benchmark knobs |
| `GET /scenarios/{id}` | Scenario summary |
| `GET /scenarios/{id}/network` | Hubs and lanes as GeoJSON, for the map |
| `GET /scenarios/{id}/disruptions` | Known disruptions |
| `GET /scenarios/{id}/detections` | Flags, filterable by `kind`, `hub` and `priority`, with `grace_h`, paging and counts |
| `GET /scenarios/{id}/shipments/{sid}` | A shipment's booking, visible scans, detection and plan choices |
| `GET /scenarios/{id}/hubs/{hub}` | Parcels stuck at a hub, and departures with spare room in the next `window_h` |
| `POST /scenarios/{id}/plans` | Run detection → options → `cpsat` / `greedy` / `none` for stranded parcels (`include_at_risk` adds AT_RISK ones); returns a summary |
| `GET /scenarios/{id}/plans[/{plan_id}]` | Saved plans; one plan's choices, filterable by `mode` |
| `GET /scenarios/{id}/shipments/{sid}/options` | Live recovery options for a flagged shipment, against capacity still free |
| `POST /scenarios/{id}/decisions` | `ACCEPT` a plan's choice or a live option, `DISMISS` a false flag, or `REVOKE` the decision in force; 409 if the capacity is gone |
| `GET /scenarios/{id}/decisions` | Decisions in force |
| `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Sign in (sets the session cookie), sign out, and who is signed in |
| `GET /metrics` | Prometheus metrics (loopback or bearer token) |
| `GET /scenarios/{id}/events` | Server-sent events: `decision` and `plan` as each one commits, `resync` if any may have been missed |
| `GET /scenarios/{id}/evaluation/...` | Detection score, a plan's replay, and a replay of the committed decisions, all against ground truth |
| `GET /lab/shift` | A simulated shift worked hour by hour by Piggyback and next service: timelines, every booking and break, and end-of-shift results (reads ground truth) |

**Decisions and capacity.**
- **Append-only log:** decisions are never edited, and the latest row per shipment is the one in force.
- **Holds:** an accepted recovery holds spare kg and m³ on the legs it rides. The hold is a database view derived from the decisions, so it can't drift out of sync with them.
- **Planning:** plans and live options use the capacity left after holds, and a new plan only covers shipments nobody has decided on.
- **No overbooking:** accepting locks the legs involved in leg-ID order, so two operators approving onto the same truck at the same moment can't overbook it or deadlock. A test races two threads to prove it.
- **Live updates:** database triggers announce every committed decision and plan with Postgres `NOTIFY`, whoever wrote it. Each API process `LISTEN`s on one connection and relays the events to browsers as server-sent events, so operators see each other's work within a moment, even across API processes. Events carry ids only and clients re-fetch. After a dropped connection, or a client too slow to keep up, clients are told to re-sync, because events sent meanwhile are lost.

**Accounts and access.**
- **Sign-in:** `POST /auth/login` checks the password against a salted scrypt hash and sets an HttpOnly, SameSite=Lax session cookie, valid 12 hours. Only the token's SHA-256 is stored, so a leaked sessions table can't be replayed. `POST /auth/logout` ends the session server-side; changing a password or disabling a user ends all their sessions.
- **Roles:** operators can decide, plan and create scenarios; viewers can see everything an operator sees but change nothing (403). Each decision records the signed-in user; whatever the request body claims is ignored.
- **Abuse:** a wrong password and an unknown username get the same answer in about the same time, failed sign-ins are throttled per client and username (5 per 10 minutes, then 429), and changes carrying a foreign `Origin` header are refused.
- **Accounts** are managed with `python -m db user add | passwd | list | disable | enable | remove`. Set `PIGGYBACK_SECURE_COOKIES=1` behind HTTPS.

**Operations.**
- **Metrics:** `GET /metrics` serves Prometheus metrics: request rate and latency per route template, requests in flight, plans (count and solve time per solver), decisions by action, capacity conflicts, live-update subscribers, whether the Postgres listener is up, and database pool use. It's open to loopback clients, or to anyone with `Authorization: Bearer $PIGGYBACK_METRICS_TOKEN` when that is set.
- **Tracing:** every response carries an `X-Request-ID` (the caller's, if it's sane). Every request is logged as one JSON line with that id, the route, the status, the signed-in user and its timings.
- **Load:** `python scripts/loadtest.py` runs many operators working one queue at once, plus browsers on the live stream ([results/LOAD.md](results/LOAD.md)). On a laptop, 100 operators pausing about 3 s between actions see request p95 under 0.4 s (once caches are warm). Every decision reached every open browser, with p95 351 ms. Four worker processes give 2.2× the throughput of one.
- **A fix the load test found:** in one process, the default 40-thread request pool starved the event loop that writes live updates (p50 4.9 s). Capping it at 4 threads (`PIGGYBACK_THREADS`) kept throughput the same and brought the p50 to 0.6 s.

Only the `/evaluation/` and `/lab/` routes read ground truth. A test checks that no operator route returns a truth field.

## Web app

`web/` is a React 19 + TypeScript app built with Vite, Tailwind and shadcn/ui, using TanStack Query for data, and MapLibre with deck.gl for maps. `npm run build` writes it to `api/static`, and the API serves it at `/app/` (`/` redirects there). For development, `npm run dev` in `web/` proxies API calls to `localhost:8000`.

- **Queue** (home): the operator's work list. Flagged parcels are sorted by how soon they're due, and can be filtered by priority and kind, and split into open, watching (at risk) and decided. The recovery card shows:
  - what happened, with the detector's evidence and recent scans
  - the plan's recommendation and the live alternatives, each with its arrival against the deadline, its cost, and how full the fullest truck gets after loading
  - a one-line reason for the recommendation: the lowest money spent plus lateness, compared with the fastest alternative

  Operators can **accept** it, **pick an alternative**, or **dismiss** a false alarm. If another accepted recovery took the space first, the API returns 409 and the card refreshes its options. A mini map shows the original booking (dotted) and the recovery (bold). Keyboard: J/K move, A accept, D dismiss, 1–9 pick an option.
- **Network:** India with its states and neighbours, the lanes drawn as gentle curves, hubs sized by parcels needing a decision, and the latest plan's piggyback hops (width = parcels) with traffic flowing along them in the direction of travel (still if the system asks for reduced motion). Dedicated trucks are dashed, lane delays and hub closures use the reserved status colours with icons, and clicking a hub lists its next 24 h of departures with free capacity.
- **Plans:** run CP-SAT, greedy or do-nothing, optionally including at-risk parcels. Browse saved plans and their choices, and read the full decision log. A dashed "lab only" panel replays a plan against the ground truth.
- **Replay:** a whole shift worked hour by hour by Piggyback and by next-service rebooking, on the same simulated day (`GET /lab/shift`, computed in a few seconds and cached). A time slider (play at 1×, 3× or 6×) moves both through the shift: the map shows the chosen policy's recoveries in flight, dedicated trucks, closures and lane delays, beside how many parcels wait for a decision, how many bookings broke and the money spent. The day's end-of-shift result is set against all 20 days of the shift benchmark. It is marked lab only, since the simulator's truth decides which bookings break.
- **Benchmark:** the head-to-head results from `/benchmark`: the headline with its confidence interval, the stress plot, where the gain comes from, all policies, results by capacity tightness, and the method.
- **Lab:** the scenario list; generating new ones (including the stress conditions); and detection precision and recall against the truth.

A Live badge in the header shows the event stream's state. When another session decides or plans, the queue, map and plans refresh in place, and a short notice says what changed. Times show in IST, relative to each scenario's simulated "now". Light and dark themes are both supported. On phones the side rail becomes a bottom tab bar, panels stack, tapping a parcel or hub scrolls its detail into view, and the map legend folds away. Only the plan replay and detection scoring read ground truth, and both are opt-in and visibly marked as lab features. The base map is built from Natural Earth's India point-of-view boundaries (`python scripts/build_basemap.py`, 171 KB, committed): no tile server or API key, it works offline, and India's borders match the official map of India rather than OpenStreetMap's.

## Layout

```
simulator/   models, hub data, generator, CSV/JSON export, CLI
detection/   operator view (no truth), detector, scoring, CLI
recovery/    planner's timetable, options, CP-SAT/greedy optimizer, replay vs truth, CLI
api/         FastAPI app and the scenario store behind it; serves the built web app from static/
web/         React + TypeScript web app (Vite, Tailwind, shadcn/ui, TanStack Query, MapLibre + deck.gl)
db/          migrations/, migration runner, repository, `python -m db` admin CLI
benchmark/   baseline + ablation policies, grid runner, report, rolling shift engine
results/     benchmark.csv + REPORT.md, stress.csv + ROBUSTNESS.md, shift.csv + SHIFT.md, scaling, load
scripts/     eval_detection.py, calibrate_detection.py, start_docker.ps1 (Windows Docker Desktop helper)
.github/     CI: lint, tests against a PostGIS service container, benchmark smoke run
tests/       simulator, migrations, Postgres repository (incl. a concurrency race), detection, recovery, API, benchmark
docs/        DECISIONS.md
```

## Known limitations

- The CLIs (`simulator.cli`, `detection.cli`, `recovery.cli`) still work on CSV folders so benchmarks run without a database. The API and web app need Postgres.
- Detections aren't stored: they're recomputed (and cached) from the scans, which is cheap and keeps them consistent with the detector's code.
- The API has no authentication, and it allows cross-origin requests only from localhost. It's meant for local use.
- The end-to-end tests cover each page's main path (accept, pick an alternative, dismiss, map layers, hub panel, benchmark, form validation) and fail on any browser console error. One test drives two browser sessions: a decision in one appears in the other without a reload. Every page is also checked with axe against WCAG 2.1 A/AA in light and dark themes (the WebGL map canvas is excluded; its hubs are keyboard-focusable buttons). Sign-in is tested as a user would do it (wrong password, right password, sign out), and a viewer is checked to be read-only. A phone project (Pixel 7, touch) walks every page through the bottom tab bar, checks nothing scrolls sideways, runs axe again, and accepts a recovery by tap. They don't cover every edge case.
- Accounts are local to the database: there is no single sign-on, password reset by email, or per-scenario permissions. The sign-in throttle is kept in memory, per API process.
- The replay checks each parcel's own plan against the truth, but doesn't re-simulate the whole network afterwards. A recovered parcel doesn't change other parcels' outcomes.
- About 4% of hub pairs can't be completed before the timetable ends at hour 144 (long cross-country trips with several transfers). For those, recovery can only use a dedicated truck.
- Road distances and corridor drive times are real (OSRM over OpenStreetMap, committed in `simulator/data/road_matrix.json`), but truck speed is scaled to an assumed 45 km/h network average. Vehicle costs and the ₹250 value of a late hour are assumed figures. Spare capacity is sampled rather than derived from real loads. The calibration numbers in `docs/DECISIONS.md` describe these assumptions, not real-world carriers.

## Data credits

Country and state boundaries come from [Natural Earth](https://www.naturalearthdata.com/) (public domain), using its India point-of-view edition.

Road distances and drive times between hubs come from the [OSRM](https://project-osrm.org/) table service over [OpenStreetMap](https://www.openstreetmap.org/copyright) data, © OpenStreetMap contributors, available under the Open Database License.
