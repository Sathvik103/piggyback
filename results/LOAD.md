# Piggyback load test

How the API holds up when many operators work one scenario's queue at once, measured end to end from the client with `python scripts/loadtest.py`. Every number below comes from a run on one developer laptop: the API, the load generator and Postgres (in Docker) shared 16 logical CPUs on Windows 11. A Linux server with the database on its own host would do better. These numbers show behaviour and relative changes, not capacity planning.

## Summary

| Scenario | Requests/s | Decisions/s | Request p95 | Live update p95 | Delivered | Errors |
|---|---|---|---|---|---|---|
| Realistic: 100 operators pausing ~3 s, 4 workers | 52 | 9.9 | 203–366 ms (queue: see below) | 351 ms | 100% | 0 |
| Stress: 20 operators, no pauses, 4 workers | 87 | 17.4 | 290–438 ms | 405 ms | 100% | 0 |
| Stress: 20 operators, no pauses, 1 process | 39 | 7.7 | 587–775 ms | 761 ms | 100% | 0 |

- **Live updates kept up in every run:** every decision reached every one of up to 100 open browsers. The "live update" time runs from the operator's POST being sent to a viewer receiving the event, so it includes the POST itself.
- **Four worker processes give 2.2× the throughput of one.** Request handling is CPU-bound Python, so more processes scale where threads can't. Each worker LISTENs to Postgres on its own, so live updates work across workers.
- **The queue's p95 in the realistic run (1.5 s) is a cold start.** The run begins with 100 operators opening the queue at the same instant against 4 fresh workers. Each worker computes detections once and caches them (p50 150 ms).
- **No capacity conflicts happened**, because random operators rarely race for the last space on the same truck. The no-overbooking guarantee is proven separately by a test that races two accepts onto one leg (`tests/test_db.py`).

## A fix this test found

The first single-process stress run delivered live updates with a **p50 of 4.9 s, and 2% arrived more than 3 s late**. Request handlers run in a thread pool, 40 threads by default. Under load those CPU-bound threads starved the event loop that writes the event streams, because they share Python's GIL. Capping the pool at 4 threads (`PIGGYBACK_THREADS`, default 4) kept throughput the same and made the streams prompt:

| One process, 20 operators, 50 viewers, no pauses | Requests/s | Live update p50 | Live update p95 | Delivered within 3 s |
|---|---|---|---|---|
| Default thread pool (40) | 43 | 4,877 ms | 7,556 ms | 98.1% |
| Capped at 4 threads | 39 | 613 ms | 761 ms | 100% |

## How the test works

Each virtual operator signs in and loops:
1. list the open queue;
2. open a random parcel and load its live options;
3. accept the cheapest recovery (or dismiss the flag);
4. revoke it, so the scenario stays the same throughout.

Viewers only hold the event stream open. Clients run in separate processes: a single Python process can't parse this many responses and event streams. The busiest client process is reported, to show the clients were never the bottleneck. All processes sign in, connect their viewers and then start together on a barrier. Only decisions sent inside the measurement window count towards delivery.

With several workers, each `/metrics` scrape answers from one worker, so scrape each worker or run one process per container.

## Runs

### Realistic: 100 operators pausing about 3 s between actions, four API worker processes (`uvicorn --workers 4`)

100 operators (pausing ~3 s between actions) and 100 live viewers, 60 s, clients spread over 8 + 8 processes (busiest at 9% of a core, so the clients were not the bottleneck). 16 logical CPUs, Windows 11, Postgres in Docker on the same machine, 2026-11-15.

- **Throughput:** 52 requests/s; 9.9 decisions committed/s (plus as many revokes).
- **Capacity conflicts:** 0 of 595 accepts or dismisses (0.0%) got 409 because another operator had just taken the space.
- **Live updates:** 108,600 of 108,600 expected deliveries (100.0%) reached the 100 viewers; POST sent to viewer received: p50 139 ms, p95 351 ms, p99 478 ms.

| Request | Count | p50 (ms) | p95 (ms) | p99 (ms) | Errors |
|---|---|---|---|---|---|
| GET detections (queue) | 658 | 150 | 1506 | 1801 | 0 |
| GET options | 627 | 92 | 238 | 325 | 0 |
| GET shipment | 627 | 56 | 203 | 265 | 0 |
| POST decision (accept) | 595 | 177 | 366 | 434 | 0 |
| POST decision (revoke) | 595 | 80 | 263 | 346 | 0 |

### Stress: 20 operators with no pauses, four API worker processes

20 operators (no pauses) and 50 live viewers, 60 s, clients spread over 4 + 4 processes (busiest at 30% of a core, so the clients were not the bottleneck). 16 logical CPUs, Windows 11, Postgres in Docker on the same machine, 2026-11-15.

- **Throughput:** 87 requests/s; 17.4 decisions committed/s (plus as many revokes).
- **Capacity conflicts:** 0 of 1,042 accepts or dismisses (0.0%) got 409 because another operator had just taken the space.
- **Live updates:** 103,550 of 103,550 expected deliveries (100.0%) reached the 50 viewers; POST sent to viewer received: p50 222 ms, p95 405 ms, p99 501 ms.

| Request | Count | p50 (ms) | p95 (ms) | p99 (ms) | Errors |
|---|---|---|---|---|---|
| GET detections (queue) | 1,056 | 243 | 403 | 570 | 0 |
| GET options | 1,048 | 215 | 351 | 443 | 0 |
| GET shipment | 1,048 | 175 | 290 | 361 | 0 |
| POST decision (accept) | 1,042 | 291 | 438 | 518 | 0 |
| POST decision (revoke) | 1,042 | 181 | 299 | 355 | 0 |

### Stress: 20 operators with no pauses, one API process (`uvicorn`)

20 operators (no pauses) and 50 live viewers, 60 s, clients spread over 4 + 4 processes (busiest at 17% of a core, so the clients were not the bottleneck). 16 logical CPUs, Windows 11, Postgres in Docker on the same machine, 2026-11-15.

- **Throughput:** 39 requests/s; 7.7 decisions committed/s (plus as many revokes).
- **Capacity conflicts:** 0 of 460 accepts or dismisses (0.0%) got 409 because another operator had just taken the space.
- **Live updates:** 45,500 of 45,500 expected deliveries (100.0%) reached the 50 viewers; POST sent to viewer received: p50 613 ms, p95 761 ms, p99 850 ms.

| Request | Count | p50 (ms) | p95 (ms) | p99 (ms) | Errors |
|---|---|---|---|---|---|
| GET detections (queue) | 480 | 638 | 747 | 837 | 0 |
| GET options | 476 | 378 | 624 | 667 | 0 |
| GET shipment | 476 | 381 | 587 | 646 | 0 |
| POST decision (accept) | 460 | 592 | 775 | 845 | 0 |
| POST decision (revoke) | 460 | 601 | 696 | 727 | 0 |
