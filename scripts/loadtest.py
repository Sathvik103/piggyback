"""Load test: many operators working one scenario's queue at once.

    python scripts/loadtest.py                                   # 20 operators + 50 viewers, 60 s, local API
    python scripts/loadtest.py --operators 40 --procs 8 --out results/LOAD.md

Each virtual operator signs in (own session) and loops: list the open queue, open a
random parcel, load its live options, accept the cheapest recovery (or dismiss if
there is none), then revoke it so the scenario stays steady. Accepting onto a truck
another operator just filled correctly returns 409 and is counted, not an error.
Meanwhile `--viewers` browsers hold the live event stream open; for every decision
we measure how long it took from the POST being sent to each viewer receiving it.

The clients run in `--procs` separate processes, so the load generator itself is not
the bottleneck (one Python process tops out well below what the API can serve);
each process reports how busy it was. All timing uses the same machine's wall clock.

It needs the API running and direct database access (to create the `load-op`
account with a random password and the scenario, if missing). Run it against a
dedicated scenario: it writes decisions (and revokes them).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import platform
import random
import secrets
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db import DEFAULT_URL, Repo  # noqa: E402
from simulator import generate_scenario  # noqa: E402

USER = "load-op"


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


# ------------------------------------------------------------------ one client process

class Stats:
    def __init__(self):
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.status: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.sent: dict[int, float] = {}               # decision_id -> wall time its POST was sent
        self.received: list[tuple[int, float]] = []    # (decision_id, wall time a viewer got it)
        self.decisions = 0
        self.conflicts = 0
        self.viewers_connected = 0

    def record(self, name: str, t0: float, r: httpx.Response) -> None:
        self.lat[name].append(time.time() - t0)
        self.status[name][r.status_code] += 1


async def _sign_in(base: str, password: str) -> httpx.Cookies:
    """One sign-in per process; its clients share the session (scrypt is deliberately slow)."""
    async with httpx.AsyncClient(base_url=base, timeout=60) as c:
        (await c.post("/auth/login", json={"username": USER, "password": password})).raise_for_status()
        return c.cookies


def _client(base: str, cookies: httpx.Cookies) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base, timeout=60, cookies=cookies, limits=httpx.Limits(max_connections=4))


async def _operator(c: httpx.AsyncClient, sid: str, st: Stats, stop: float, rng: random.Random,
                    think: float) -> None:
    async def pause() -> bool:  # a person reading the screen: think x 0.5-1.5; False once time is up
        if think:
            await asyncio.sleep(think * rng.uniform(0.5, 1.5))
        return time.time() < stop

    while time.time() < stop:
        t = time.time()
        r = await c.get(f"/scenarios/{sid}/detections", params={"status": "open", "limit": 5000})
        st.record("GET detections (queue)", t, r)
        items = [d for d in r.json()["items"] if d["kind"] != "AT_RISK"]
        if not items:
            await asyncio.sleep(0.2)
            continue
        ship = rng.choice(items)["shipment_id"]
        if not await pause():
            break
        t = time.time()
        st.record("GET shipment", t, await c.get(f"/scenarios/{sid}/shipments/{ship}"))
        t = time.time()
        r = await c.get(f"/scenarios/{sid}/shipments/{ship}/options")
        st.record("GET options", t, r)
        opts = [o for o in r.json() if o["mode"] != "NONE"] if r.status_code == 200 else []
        best = min(opts, key=lambda o: o["penalty_inr"]) if opts else None
        if not await pause():
            break
        body = ({"shipment_id": ship, "action": "ACCEPT", "option_id": best["option_id"]} if best
                else {"shipment_id": ship, "action": "DISMISS"})
        t = time.time()
        r = await c.post(f"/scenarios/{sid}/decisions", json=body)
        st.record(f"POST decision ({body['action'].lower()})", t, r)
        if r.status_code == 201:
            st.sent[r.json()["decision_id"]] = t
            st.decisions += 1
            await pause()  # always revoke, even after time is up, so the scenario stays steady
            t = time.time()
            r2 = await c.post(f"/scenarios/{sid}/decisions", json={"shipment_id": ship, "action": "REVOKE"})
            st.record("POST decision (revoke)", t, r2)
            if r2.status_code == 201:
                st.sent[r2.json()["decision_id"]] = t
        elif r.status_code == 409:
            st.conflicts += 1


async def _viewer(c: httpx.AsyncClient, sid: str, st: Stats, until: list[float]) -> None:
    async with c.stream("GET", f"/scenarios/{sid}/events", timeout=httpx.Timeout(None, connect=30)) as r:
        st.viewers_connected += 1
        name = None
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: ") and name == "decision":
                st.received.append((json.loads(line[6:])["decision_id"], time.time()))
            if time.time() > until[0]:
                break


async def _run_proc(base, password, sid, n_ops, n_viewers, barrier, duration, seed, think) -> dict:
    st = Stats()
    rng = random.Random(seed)
    cookies = await _sign_in(base, password)
    clients = [_client(base, cookies) for _ in range(n_ops + n_viewers)]
    until = [float("inf")]
    viewers = [asyncio.create_task(_viewer(c, sid, st, until)) for c in clients[n_ops:]]
    for _ in range(300):  # wait (up to 30 s) until every viewer's stream is open
        if st.viewers_connected == n_viewers:
            break
        await asyncio.sleep(0.1)
    await asyncio.get_running_loop().run_in_executor(None, barrier.wait)  # every process ready: go
    start = time.time()
    until[0] = start + duration + 3
    cpu0 = time.process_time()
    await asyncio.gather(*(_operator(c, sid, st, start + duration, random.Random(rng.random()), think)
                           for c in clients[:n_ops]))
    if not n_ops:
        await asyncio.sleep(duration)
    cpu = (time.process_time() - cpu0) / max(time.time() - start, 1e-9)
    await asyncio.sleep(3)  # let the last events arrive
    for v in viewers:
        v.cancel()
    await asyncio.gather(*viewers, return_exceptions=True)
    for c in clients:
        await c.aclose()
    return {"lat": dict(st.lat), "status": {k: dict(v) for k, v in st.status.items()}, "sent": st.sent,
            "received": st.received, "decisions": st.decisions, "conflicts": st.conflicts,
            "viewers": st.viewers_connected, "cpu": cpu, "start": start}


def _proc_main(args) -> dict:
    return asyncio.run(_run_proc(*args))


def _split(n: int, k: int) -> list[int]:
    return [n // k + (1 if i < n % k else 0) for i in range(k)]


# ------------------------------------------------------------------ orchestration and report

def ensure_fixtures(db_url: str, sid: str, n_shipments: int) -> str:
    password = secrets.token_urlsafe(18)
    repo = Repo(db_url, max_size=1)
    try:
        repo.remove_user(USER)
        repo.add_user(USER, "Load test", "operator", password)
        if not repo.exists(sid):
            repo.save_scenario(sid, generate_scenario(seed=42, n_shipments=n_shipments))
    finally:
        repo.close()
    return password


def report(parts: list[dict], a: argparse.Namespace) -> str:
    lat: dict[str, list[float]] = defaultdict(list)
    status: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    sent: dict[int, float] = {}
    for p in parts:
        for k, v in p["lat"].items():
            lat[k] += v
        for k, v in p["status"].items():
            for code, n in v.items():
                status[k][code] += n
        sent.update(p["sent"])
    # Only decisions sent inside the measurement window: viewers listen until 3 s after it.
    window_end = min(p["start"] for p in parts) + a.duration
    sent = {i: t for i, t in sent.items() if t <= window_end}
    live = [1000 * (t - sent[i]) for p in parts for i, t in p["received"] if i in sent]
    decisions = sum(p["decisions"] for p in parts)
    conflicts = sum(p["conflicts"] for p in parts)
    viewers = sum(p["viewers"] for p in parts)
    expected = viewers * len(sent)
    total = sum(sum(v.values()) for v in status.values())
    rows = []
    for name in sorted(lat):
        xs = [1000 * x for x in lat[name]]
        st = status[name]
        errors = sum(v for k, v in st.items() if k >= 500 or (k >= 400 and k != 409))
        rows.append(f"| {name} | {sum(st.values()):,} | {pct(xs, 50):.0f} | {pct(xs, 95):.0f} | {pct(xs, 99):.0f} "
                    f"| {errors} |")
    attempts = decisions + conflicts
    cpu = max((p["cpu"] for p in parts if p["lat"]), default=0.0)
    vcpu = max((p["cpu"] for p in parts if not p["lat"]), default=0.0)
    busiest = max(cpu, vcpu)
    verdict = ", so the clients were not the bottleneck" if busiest < 0.8 else "; warning: clients near saturation"
    machine = f"{os.cpu_count()} logical CPUs, {platform.system()} {platform.release()}"
    return "\n".join([
        f"### {a.server}",
        "",
        f"{a.operators} operators ({'no pauses' if not a.think else f'pausing ~{a.think:g} s between actions'}) "
        f"and {a.viewers} live viewers, {a.duration:.0f} s, clients spread over "
        f"{a.procs} + {a.viewer_procs} processes (busiest at {100 * busiest:.0f}% of a core{verdict}). "
        f"{machine}, Postgres in Docker on the same machine, {date.today().isoformat()}.",
        "",
        f"- **Throughput:** {total / a.duration:,.0f} requests/s; {decisions / a.duration:,.1f} decisions committed/s "
        "(plus as many revokes).",
        f"- **Capacity conflicts:** {conflicts:,} of {attempts:,} accepts or dismisses "
        f"({100 * conflicts / max(attempts, 1):.1f}%) got 409 because another operator had just taken the space.",
        f"- **Live updates:** {len(live):,} of {expected:,} expected deliveries "
        f"({100 * len(live) / max(expected, 1):.1f}%) reached the {viewers} viewers; POST sent to viewer received: "
        f"p50 {pct(live, 50):.0f} ms, p95 {pct(live, 95):.0f} ms, p99 {pct(live, 99):.0f} ms.",
        "",
        "| Request | Count | p50 (ms) | p95 (ms) | p99 (ms) | Errors |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
    ])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default=os.environ.get("PIGGYBACK_URL", "http://localhost:8000"))
    ap.add_argument("--db-url", default=DEFAULT_URL)
    ap.add_argument("--scenario", default="loadtest")
    ap.add_argument("--n-shipments", type=int, default=3000)
    ap.add_argument("--operators", type=int, default=20)
    ap.add_argument("--viewers", type=int, default=50)
    ap.add_argument("--duration", type=float, default=60)
    ap.add_argument("--procs", type=int, default=4, help="processes for the operators")
    ap.add_argument("--viewer-procs", type=int, default=4, help="separate processes for the viewers")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--think", type=float, default=0.0,
                    help="mean seconds an operator pauses between actions (0 = stress test)")
    ap.add_argument("--server", default="One API process (`uvicorn`)", help="how the API was run, for the report")
    ap.add_argument("--out", type=Path, help="append the report section to this file")
    a = ap.parse_args()

    password = ensure_fixtures(a.db_url, a.scenario, a.n_shipments)
    # Operators and viewers in separate processes: reading event streams is CPU-heavy
    # for a Python client and would otherwise slow the operators down.
    ctx = mp.get_context("spawn")
    with ctx.Manager() as manager:
        n_jobs = (sum(1 for n in _split(a.operators, a.procs) if n)
                  + sum(1 for n in _split(a.viewers, a.viewer_procs) if n))
        barrier = manager.Barrier(n_jobs)  # all processes start the clock together, once connected
        jobs = [(a.url, password, a.scenario, n, 0, barrier, a.duration, a.seed * 1000 + i, a.think)
                for i, n in enumerate(_split(a.operators, a.procs)) if n]
        jobs += [(a.url, password, a.scenario, 0, n, barrier, a.duration, 0, 0.0)
                 for n in _split(a.viewers, a.viewer_procs) if n]
        with ctx.Pool(len(jobs)) as pool:
            parts = pool.map(_proc_main, jobs)
    text = report(parts, a)
    print(text)
    if a.out:
        with open(a.out, "a", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"appended to {a.out}")


if __name__ == "__main__":
    main()
