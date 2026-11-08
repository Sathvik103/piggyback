"""Run every policy on a grid of scenarios and replay each plan against the truth.

    python -m benchmark.run                     # full grid -> results/benchmark.csv + results/REPORT.md
    python -m benchmark.run --quick             # 3 seeds, for a smoke test
    python -m benchmark.run --seeds 1-10 --tightness 0.9 --workers 4

One row per (scenario, policy). Scenarios run in parallel processes; every
number is reproducible from the seed and knobs in its row.
"""

from __future__ import annotations

import argparse
import csv
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from detection import OperatorView, detect
from recovery import replay
from simulator import generate_scenario

from .policies import POLICIES, reactive_followup

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
DEFAULT_SEEDS = range(1, 21)
DEFAULT_TIGHTNESS = (0.3, 0.6, 0.9)
DEFAULT_ANOMALY = (0.02, 0.04, 0.08)
KPI_FIELDS = ("stranded", "on_time", "late", "failed", "unrecovered", "on_time_rate", "weighted_late_h",
              "cost_inr", "unnecessary", "unnecessary_cost_inr", "realized_penalty_inr")
FIELDS = ("seed", "capacity_tightness", "anomaly_rate", "policy", "family", "flagged", "plan_s", *KPI_FIELDS)


def run_case(seed: int, tightness: float, anomaly: float, policies: tuple[str, ...]) -> list[dict]:
    sc = generate_scenario(seed=seed, capacity_tightness=tightness, anomaly_rate=anomaly)
    view = OperatorView.from_scenario(sc)
    dets = detect(view)
    delays = {d.trip_id: d.delay_h for d in sc.trip_delays}
    rows = []
    for p in POLICIES:
        if p.name not in policies:
            continue
        t0 = time.perf_counter()
        plan = p.run(view, dets)
        plan_s = time.perf_counter() - t0
        if p.followup:
            plan = reactive_followup(view, dets, plan, delays, p.followup)
        k = replay(view, sc.truth, delays, dets, plan)[1]
        rows.append({"seed": seed, "capacity_tightness": tightness, "anomaly_rate": anomaly, "policy": p.name,
                     "family": p.family, "flagged": len(dets), "plan_s": round(plan_s, 3),
                     **{f: k[f] for f in KPI_FIELDS}})
    return rows


def _parse_seeds(s: str) -> list[int]:
    out: list[int] = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def main(argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seeds", type=_parse_seeds, default=list(DEFAULT_SEEDS), help="e.g. 1-20 or 1,5,9")
    ap.add_argument("--tightness", type=float, nargs="+", default=list(DEFAULT_TIGHTNESS))
    ap.add_argument("--anomaly", type=float, nargs="+", default=list(DEFAULT_ANOMALY))
    ap.add_argument("--policies", nargs="+", default=[p.name for p in POLICIES])
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--quick", action="store_true", help="seeds 1-3, tightness 0.6, anomaly 0.04")
    ap.add_argument("--out", type=Path, default=RESULTS_DIR / "benchmark.csv")
    a = ap.parse_args(argv)
    if a.quick:
        a.seeds, a.tightness, a.anomaly = [1, 2, 3], [0.6], [0.04]

    cases = [(s, t, an) for t in a.tightness for an in a.anomaly for s in a.seeds]
    pols = tuple(a.policies)
    print(f"{len(cases)} scenarios x {len(pols)} policies on {a.workers} workers")
    t0 = time.perf_counter()
    rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futs = {pool.submit(run_case, *c, pols): c for c in cases}
        for i, f in enumerate(as_completed(futs), 1):
            rows.extend(f.result())
            if i % 10 == 0 or i == len(cases):
                print(f"  {i}/{len(cases)} scenarios done ({time.perf_counter() - t0:.0f}s)")
    order = {p.name: i for i, p in enumerate(POLICIES)}
    rows.sort(key=lambda r: (r["capacity_tightness"], r["anomaly_rate"], r["seed"], order[r["policy"]]))

    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {a.out} ({len(rows)} rows, {time.perf_counter() - t0:.0f}s)")

    from .report import write_report
    report = write_report(a.out)
    print(f"wrote {report}")
    return a.out


if __name__ == "__main__":
    main()
