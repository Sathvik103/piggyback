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
| Web app (step 7) | React 19 + TypeScript, Vite, Tailwind + shadcn/ui, TanStack Query, MapLibre GL 6; served by the API at `/app/` | Rebuilt from a one-page dashboard into an operator console after design review. The queue and recovery-card workflow comes first. No basemap tiles: works offline and avoids contested borders |
| Tests | pytest | |

Times are stored as **hours since scenario start** (floats), not timestamps. This keeps the simulator, SQL and the optimizer in the same unit. The dashboard can map them to wall-clock time.

## Scope (7 steps)

1. **Schema + seeded simulator** (done): the world, scan histories, strandings, disruptions and ground truth.
2. **Detection** (done): flag stranded or misplaced shipments using only `scan_events`, the timetable and `disruptions`. Score precision and recall against `sim_truth`. See "Detection" below.
3. **Option generation** (done): for each flagged shipment, list feasible recovery itineraries over trip legs departing after `now`. Respect spare kg/m³, connection times and disruptions.
4. **Optimizer** (done): choose one option per shipment (or none) under shared leg capacity. Minimise priority-weighted lateness plus cost.
5. **Replay benchmark** (done, first version): run 2–4 over seeds × capacity tightness. Compare against greedy and do-nothing baselines. See "Recovery" below.
6. **API** (done): serve scenarios, detections and plans. Ground truth only under `/evaluation/` and `/lab/` routes (see README).
7. **Dashboard** (done): map of hubs and lanes, stranded shipments, the proposed plan and its KPIs. Truth-based numbers are opt-in and visually separated (see README).

Out of scope: real carrier integrations, last-mile delivery, pricing, live routing (road distances come from a committed OSRM matrix; see below).

## Information boundary

Detection and recovery code may read: `hubs`, `lanes`, `corridors`, `trips`, `trip_legs`, `shipments`, `scan_events`, `disruptions`. It must never read the `sim_*` tables (`sim_truth`, `sim_trip_delays`) or `Scenario.truth` / `Scenario.trip_delays`. Those exist only to score step 2 and to replay recovery in step 5. In code, detection reads through `detection.OperatorView`, which has no truth fields. Its CSV loader skips the `sim_*` files, and a test checks this. In Postgres the same split holds: the repository's `view()` never reads `sim_*`, and only the API's `/evaluation/` routes call `truth()`.

**Clock time.** Each scenario has a `start_at` (default 2026-11-02 00:00 IST). Every `*_h` field is hours after it; the API's summary also gives `now_at`.

`scan_events` only holds scans with `recorded_h <= now`. `ts_h` is when the scan happened; `recorded_h` is when it reached the system. Planned future scans are not stored anywhere. They can be re-derived from `shipments.planned_legs`.

## Simulator model

- **Network**: 25 Indian hubs. Lanes are the minimum spanning tree plus each hub's 3 nearest neighbours, in both directions, so the graph is always connected (94 directed lanes with the current hub list and OSRM road distances).
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

An `AT_RISK` parcel is ready at its predicted arrival plus the 2 h minimum connection, so it can be re-booked before it is actually stranded. Plans leave AT_RISK parcels out by default (see Benchmark design); `include_at_risk` puts them back in.

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

**Transfer buffer.** Most failed recoveries came from transferring off a truck whose delay nobody had observed yet; it turned out later than planned and the connection broke. The planner therefore demands extra slack (`UNOBSERVED_TRANSFER_BUFFER_H`) when a plan changes trucks after one with no scans yet. Trucks with observed delays need none, and the replay and the baselines never use it. The value, 1.5 h, was picked on validation seeds 101–110 × tightness 0.6/0.9 (penalty −5.5% vs no buffer there, failures 4.9% → 2.1%). The reported seeds 1–20 were never used for tuning.

On those reported seeds the buffer more than halves failed recoveries (4.4% → 2.0%) and saves −3.3% ± 2.1 penalty.

**Re-checked after the switch to real road times** (OSRM), again only on validation seeds 101–110 × 0.6/0.9, sweeping 0–2.5 h: failed recoveries fall steadily as the buffer grows (3.5% at 0.5 h, 2.1% at 1.5 h, 0.6% at 2.5 h), but the penalty versus no buffer moves within noise (−1.4% to +3.1%, no trend). With no penalty signal to tune on, 1.5 h was kept rather than re-fitted to noise. So the buffer is described as a reliability feature, not a cost saver.

**Risk pricing replaced the buffer as the default.** The whole-shift benchmark (below) showed the fixed buffer losing to next-service rebooking (+7.1% penalty, ±14.0, better in only 10 of 20 shifts). On seed 1 the first suspicion was the opposite, that Piggyback's zero-slack plans broke too often; measuring it showed Piggyback's plans were already *more* reliable than next-service's. The real cost was the connections the buffer threw away: a hard 1.5 h rule rejects a transfer with an hour of slack that holds most of the time, and the parcel then rides something hours later, or a truck.

`recovery/risk.py` replaces the rule with a price:
- **Delay model.** The empirical distribution of every delay the operator has observed on trucks that already ran (`observed_trip_delays`, never the truth). With fewer than 30 observations it applies no risk at all.
- **Chance it holds.** For each transfer after an unobserved truck, P(the truck's delay ≤ the slack), multiplied over the route.
- **Expected lateness** = P(ok) × E[late | ok] + (1 − P(ok)) × max(`BROKEN_LATENESS_H`, planned lateness). E[late | ok] adds the expected excess delay of the last truck if it hasn't been observed.
- **Weighted by P(stranded).** Money is spent for sure; lateness is only saved if the flag is a real stranding. `scripts/calibrate_detection.py` measures that chance per detection kind and confidence on validation seeds 101–110 (first flag per parcel over 24 h shifts, add-one smoothing, at least 10 per bucket) and commits it as `recovery/data/calibration.json`. For example, a STALLED flag at confidence 0.6 is real 10% of the time, MISSED_CONNECTION at 0.9 is real 94%.
- **Both route sets.** Options come from the network with and without the buffer, merged (one copy of each distinct route), so the optimizer chooses between a safe route and a tight one on expected penalty.

`BROKEN_LATENESS_H = 12` is the one knob, picked on validation seeds 101–110 × 0.6/0.9 over whole shifts: −9.3% penalty vs next-service there, better in 17/20, and flat from 6 to 24 h, so it's not a sharp optimum. The reported seeds were never used.

It became the default only after it won on the reported seeds too, in both benchmarks: −5.0% (±1.4) vs the fixed buffer on the snapshot grid (135/180) and −15.2% (±5.7) over whole shifts (17/20). The trade-off is disclosed: failed recoveries rise from 2.0% to 2.8%, because some of the connections it takes do break; it makes that up with +1.8 pp on time and fewer parcels left unrecovered. The fixed buffer is kept as the ablation `piggyback_fixed_buffer`. Stranding calibration on its own is worth only −0.2% (±0.1) on the snapshot grid: with detection precision at 92% there are few doubtful flags.

**Results.** See "Benchmark design" below, `results/REPORT.md` and `results/ROBUSTNESS.md`. About 2% of Piggyback's plans still fail in replay, mostly from the occasional wrong hub from detection and from trucks whose delay grows after it was observed.

## Live updates between operators

Several operators can work one scenario. Accepting already locks capacity, so their decisions can't overbook a truck, but without live updates each of them would only see the others' work after a refresh, and would keep opening parcels someone had already handled.

- **Source of the events.** Triggers on `decisions` and `plans` call `pg_notify` (migration 004). Postgres delivers a notification only when its transaction commits, in commit order, so nothing is announced that could still roll back. Triggers rather than application code mean every writer is covered: any API process, the CLI or a script.
- **Relay.** Each API process holds one extra connection that `LISTEN`s, and fans events out to the browsers watching that scenario over server-sent events. SSE was preferred to WebSockets because traffic is one-way, it works through plain HTTP proxies, and `EventSource` reconnects by itself.
- **Small payloads.** Events carry ids (shipment, decision, plan), not data. Clients re-fetch through the normal routes, so the information boundary and the caching rules stay in one place, and the 8000-byte `NOTIFY` limit never matters.
- **Gaps.** `NOTIFY` is fire-and-forget. If the listener reconnects, or a client is too slow and its queue fills, the client is told to `resync` and re-fetches everything for the scenario, rather than silently missing changes.
- **Own changes.** A tab remembers the ids of decisions and plans it made, so their echo refreshes data without telling the operator about their own work.

## Accounts and sign-in

Decisions move real freight, so the log has to say who made each one, and not everyone who can look should be able to act.

- **Sessions in Postgres, not JWTs.** A random token in an HttpOnly cookie, with its SHA-256 in a `sessions` table. Signing out, changing a password or disabling a user takes effect on the next request, which a stateless token can't do without a revocation list anyway. One indexed lookup per request is cheap at this scale.
- **scrypt from the standard library.** A memory-hard, salted hash with its parameters stored next to it, so they can be raised later without breaking old hashes. It needs no extra dependency.
- **Two roles.** Operators act; viewers (a manager, an auditor, a carrier partner) see the same console read-only. The API enforces the role, and the console only mirrors it by disabling the controls.
- **One guard for every route.** A single FastAPI dependency decides access for every route, with a short allow-list of public paths, so a new route is protected by default.
- **CSRF.** SameSite=Lax cookies plus refusing state changes that carry a foreign `Origin`. JSON-only endpoints mean a plain HTML form can't post to them either.
- **No credentials in the repo.** The end-to-end setup creates its users through the CLI with fresh random passwords on every run and signs them in; the API tests do the same through a fixture.

## Observability and load

- **Metrics by route template.** Requests are labelled `/scenarios/{sid}/...`, never the raw path, so a scanner hitting random URLs can't blow up label cardinality (unmatched paths share one label). Latency is time to first byte, so the long-lived event stream doesn't skew it.
- **Pure ASGI middleware.** Starlette's `BaseHTTPMiddleware` wraps responses in a way that interferes with streaming; a plain ASGI middleware only watches the response start and passes the body through.
- **Untrusted request ids are replaced.** An incoming `X-Request-ID` is kept only if it's 1–64 safe characters, so callers can't inject newlines or junk into the logs.
- **The load generator must not be the bottleneck.** The first version ran every client in one Python process. It saturated a core and under-reported the API by about 3×, and its late-connecting viewers made deliveries look lost. The final version runs operators and viewers in separate processes, starts them together on a barrier once everyone is connected, reports how busy each was, and counts only decisions inside the measurement window.
- **Fewer threads, prompter streams.** The default 40-thread pool let CPU-bound handlers starve the event loop that pushes live updates (GIL contention): one process under stress delivered them with a 4.9 s median. Four threads gave the same throughput and a 0.6 s median. Throughput comes from worker processes instead, and the Docker image runs four.
- **Hot paths cached, capacity never.** Per-scenario lookups that never change (scan index, shipments, legs, serialized detections) are built once. Free capacity is read from Postgres on every request, because other operators' decisions change it. A shipment's decision and its choices across all plans are each one query.

## The map

- **Our own base map, not a tile service.** India, its states and its neighbours, from Natural Earth's India point-of-view edition, clipped and simplified to 171 KB (`scripts/build_basemap.py`). It needs no API key, licence or network, it themes exactly with the app in light and dark, and India's boundaries follow the official map of India. Most OpenStreetMap-based tiles don't, which an Indian user would notice.
- **MapLibre for the land, deck.gl for the network.** MapLibre draws the base map and the hub markers. deck.gl draws the lanes as curves (both directions of a lane share one curve, so a plan's hop sits exactly on its lane), plan traffic as width-weighted lines with a soft underglow, and moving pulses along each hop in its direction of travel. The pulses stay still for users who ask for reduced motion.
- **Hubs stay HTML buttons**, so they keep keyboard focus, screen-reader names and the end-to-end tests. CSS lifts them above deck.gl's canvas. On narrow maps the dots shrink and only the selected hub is labelled, so labels don't collide on a phone.
- **Two deck.gl gotchas, both caught before shipping.** Dashes are measured along the whole path: by default deck.gl 9.4 restarts them on every segment, which made finely sampled curves look solid. And a layer keeps the dash extension for life, switching only its pattern; adding and removing the extension on a live layer broke rendering, which the end-to-end tests' console check caught.
- **The moving pulses are drawn outside React.** They used to tick a React state every frame, re-rendering the whole map component. On the replay map, with hundreds of recoveries in flight, that starved React Router's navigation transitions: tapping Lab changed the URL, but the page never switched. Each frame now hands deck.gl new layers directly. A device that can't draw them smoothly (five frames over 120 ms in a row, as with software WebGL in headless test browsers, where accessibility checks slowed to four minutes) stops the pulses and keeps the lines.

## Benchmark design

A benchmark case is `(seed, capacity_tightness, anomaly_rate, n_incidents, n_disruptions, n_shipments)`. The same tuple always reproduces the same scenario (tested).

- **Detection metrics**: precision, recall and F1 against `sim_truth`. Also the hub-location accuracy (`true_hub_id`) and recall per anomaly type.
- **Recovery metrics**: share of stranded shipments recovered before their deadline, and priority-weighted hours late (EXPRESS 3, STANDARD 2, ECONOMY 1). Also incremental cost in INR, capacity used on piggybacked legs, and solve time.
- **Grid** (`python -m benchmark.run`): seeds 1–20 × `capacity_tightness ∈ {0.3, 0.6, 0.9}` × `anomaly_rate ∈ {0.02, 0.04, 0.08}` = 180 scenarios, all with `n_incidents=2` and `n_disruptions=8`. It runs in parallel processes in about 13 minutes on 8 workers.
- **Policies.** Every policy gets the same detections (grace 2 h), so differences come from recovery alone.
  - **Baselines:** do nothing; next service FCFS (published timetable, earliest service with room, in the order problems were seen, reactive); next service + a dedicated truck for any EXPRESS parcel that would be late.
  - **Piggyback** (the shipped default): delay-aware network, options priced by expected penalty, CP-SAT, acts once a parcel is stranded.
  - **Ablations:** greedy instead of CP-SAT; the fixed 1.5 h buffer instead of risk pricing; risk pricing without stranding calibration; published timetable; + proactive rebooking of AT_RISK parcels.
- **Fairness.** Reactive policies (the next-service baselines and Piggyback itself) get a second chance. An AT_RISK parcel they didn't act on, which really does miss its connection, is rebooked by their own rules 2 h after the late arrival, against the capacity left. The first version of the benchmark skipped this. It credited proactive Piggyback with +20 pp on-time over next-service, of which +16 pp was an artefact of the baselines never getting that chance.
- **Default switched after benchmarking (disclosed).** Piggyback first rebooked AT_RISK parcels proactively. The fair benchmark showed that didn't pay: it reserves capacity on a prediction that waiting confirms more cheaply. So the default became wait-then-act and the grid was re-run. Proactive rebooking is still available (`include_at_risk` / `--include-at-risk`) and is benchmarked as an ablation.
- **Statistics.** Comparisons are paired per scenario. The report shows mean differences with 95% t-intervals, and calls a difference significant only if its interval excludes 0. A sensitivity table re-prices the same outcomes at ₹50–1000 per late hour.
- **Findings** (full numbers in `results/REPORT.md` and the README):
  - **vs next service FCFS:** −31.1% realized penalty, +6.4 pp on time, and failed recoveries 2.8% vs 10.6%. Better in 177/180 scenarios.
  - **vs the truck rule:** −48.4% penalty, though 2.5 pp fewer parcels on time, because it spends about 8.6× less.
  - **Delay awareness carries most of the gain** (−25.5%).
  - **Risk pricing vs the fixed buffer:** −5.0%, +1.8 pp on time, failures 2.0% → 2.8%.
  - **CP-SAT over greedy:** −3.9% overall, −8.5% when capacity is tight.
  - **Stranding calibration:** −0.2%.
  - **Proactive rebooking:** no better than waiting on the grid (+0.6% ± 1.2, not significant), but significantly better (about 5%) in two stress conditions, so the default is a close call.
- **Robustness** (`python -m benchmark.run --stress`, `results/ROBUSTNESS.md`): the same contest under conditions Piggyback wasn't designed around. Each changes one thing from the default world: delays that start mid-trip (so early scans look on time), 3% of trips silently cancelled, spare capacity scarce on busy corridors and evenings, 3× noisier scans, and twice as many late trips. 10 seeds × tightness 0.6/0.9 per condition.
  - **Result:** Piggyback beats next service FCFS on realized penalty in every condition (−20.8% to −39.1%, all significant), with a significant on-time gain in each. Failed recoveries are 1.9–3.6× lower, and it wins 19–20 of 20 scenarios per condition. The gap is widest with twice as many late trips.
  - **Mid-trip delays are no longer the weak spot.** Under the fixed buffer they were (−17.0%, on-time gain not significant). Risk pricing is worth −9.5% there: the learned delay spread already allows for trucks that look on time and then slip.
  - **Silent cancellations** push Piggyback's failure rate to 8.3%, about half the baseline's 15.5%.
  - **The optimizer's contribution** is significant in every condition (−2.3% to −9.1%), largest when capacity is scarce at peaks.

## Whole shifts (the digital twin)

The snapshot benchmark plans once. A real control tower re-plans all shift while parcels keep stranding and bookings keep breaking, so `benchmark/shift.py` runs the same world forward:
- **The world runs on.** `ScenarioConfig.shift_h` (24 h) spreads strandings and new shipments over the shift, and disruptions start at any time up to 8 h past its end. `OperatorView.at(sc, t)` shows only scans recorded by `t`, shipments created by `t`, and disruptions announced by `t` (8 h ahead). Snapshot scenarios are unchanged (tested, and the seed-42 fingerprint is identical).
- **Every policy works hourly.** Each hour it plans the parcels that are open now. Bookings hold their space on the legs they ride for the rest of the shift.
- **Truth decides each booking's fate.** It is walked against the true timetable (real delays, real parcel location). A booking that breaks is noticed 2 h later, and the parcel reopens as a missed connection at the hub where it got stuck, for at most 3 bookings per parcel.
- **Aftercare.** Bookings that break after the shift ends are still replanned by the same policy (as the next shift would), so no policy is scored as losing a parcel just because the clock stopped. Without this, the first version charged 48 h for those parcels, which punished whichever policy booked later trucks.
- **Scoring.** Money on every booking, broken or not, plus ₹250 × priority weight × hours late, and 48 h for parcels never recovered. Only parcels that stranded at least 2 h before the shift ended count.
- **Result** (10 seeds × 0.6/0.9, `results/SHIFT.md`): −10.8% (±9.0) vs next service, better in 13 of 20; −63.9% vs the truck rule; −15.2% vs the fixed buffer.
- **Finding, disclosed.** Hourly re-planning makes first-departure rebooking a much tougher baseline than the snapshot suggests. Its bookings break twice as often as Piggyback's (52 vs 23 per shift), but each break is retried cheaply on the next truck. The snapshot's −31% does not carry over whole; −11% does, with a wide interval.
- **A bug the full run found.** The aftercare loop rounded its clock to 0.01 h. A break due at, say, 30.004 h rounded down to 30.00, was never due, and the loop spun forever on one case (seed 5, tightness 0.9) for two hours. The clock is no longer rounded, and that case is a regression test.

The console's **Replay** page (`GET /lab/shift`) plays one of these shifts for Piggyback and next service side by side, and sets the day against all 20.

## Scaling and the solver

`python -m benchmark.scaling` grows the network from 3,000 to 40,000 shipments (3 seeds each, capacity tightness 0.9, CP-SAT limit 30 s, 8 workers, one machine) and times what an operator waits for after re-planning.
- **At 3,000 shipments** (about 230 stranded parcels), CP-SAT proves optimality in under 2 s.
- **From 6,000 up** it uses the full limit. Up to 20,000 its lower bound still proves every plan is within 1.5% of the optimum (worst seed), while greedy is 17–58% worse.
- **At 40,000, risk pricing costs plan quality.** Pricing routes with and without the buffer gives CP-SAT about 15% more options (19,300 vs 16,700). Two seeds stay within 1.4% of optimal; seed 3 ends 17.1% off at 30 s. Re-run with a 90 s limit, the same seed reaches 0.6%, so it is the time limit, not the model. Before risk pricing the worst gap at this size was 1.2%. Pruning dominated options before the solve is the obvious next step; it hasn't been done.
- **Detection and option generation** grow roughly linearly, to under 12 s at 40,000 (option generation doubled, since it builds two route sets).

**Warm start tried and dropped.** Hinting the greedy plan to CP-SAT seemed obvious: it guarantees the search starts from greedy's quality. Measured on the same instances, it was neutral up to 20,000 shipments and anchored the search badly at 40,000, where the plan was 11.5% worse than a cold start and the provable gap grew to 14.5%. The shipped solver starts cold and keeps a greedy safety net instead: if a time-limited run ends worse than greedy, it returns greedy's plan (status `FEASIBLE_GREEDY`). On these instances the net never triggered.

## Calibration assumptions (not real-world data)

- Road distances and drive times between the 25 hubs are real: an OSRM table request over OpenStreetMap (car profile), committed as `simulator/data/road_matrix.json` and refreshed with `python scripts/fetch_road_matrix.py`. Real roads are 1.07–1.46× the great-circle distance (mean 1.22, against the 1.3 assumed before).
- Truck times are OSRM's car times × 1.81, the factor that makes the network-wide average line-haul speed 45 km/h including stops. That keeps the old calibrated average but lets corridors differ: a ghat road like Kochi–Coimbatore runs at 34 km/h, the Delhi–Mumbai expressway at 49 km/h. The 45 km/h average itself is still an assumption.
- Vehicles: 14 ft (2.5 t, 18 m³, ₹22/km), 20 ft (5 t, 32 m³, ₹32/km), 32 ft MXL (9 t, 60 m³, ₹48/km). These are assumed figures, not carrier quotes.
- Parcel weight is log-normal (median 8 kg, clipped to 0.2–400 kg); density is 120–300 kg/m³.
- Hub sort capacities are illustrative; nothing uses them yet.
