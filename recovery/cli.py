"""python -m recovery.cli data/scenario_42 [--solver all|cpsat|greedy|none]

Runs detection -> options -> optimizer on a scenario directory, writes
plan_<solver>.csv, and, if the scenario's truth files are present, replays each
plan against what really happened.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import fields
from pathlib import Path

from detection import DetectorConfig, OperatorView, detect
from detection.detector import KIND_AT_RISK
from detection.view import read_table

from .evaluate import replay
from .optimizer import SOLVERS, Plan, leg_capacity
from .options import OptionsConfig, RecoveryOption, generate_options

KPI_COLUMNS = ("on_time_rate", "on_time", "late", "failed", "unrecovered", "cost_inr",
               "weighted_late_h", "realized_penalty_inr")


def write_plan(plan: Plan, path: Path) -> None:
    cols = [f.name for f in fields(RecoveryOption)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for o in sorted(plan.chosen.values(), key=lambda o: o.shipment_id):
            row = []
            for c in cols:
                v = getattr(o, c)
                row.append(";".join(v) if isinstance(v, tuple) else "" if v is None else v)
            w.writerow(row)


def print_table(rows: list[dict]) -> None:
    head = ["solver", "objective_inr", "solve_s", *KPI_COLUMNS]
    print("  ".join(f"{h:>14}" for h in head))
    for r in rows:
        print("  ".join(f"{r.get(h, ''):>14}" for h in head))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Plan recovery for a scenario directory.")
    p.add_argument("scenario", type=Path)
    p.add_argument("--solver", default="all", choices=["all", *SOLVERS])
    p.add_argument("--grace", type=float, default=DetectorConfig().grace_h)
    p.add_argument("--time-limit", type=float, default=10.0, help="CP-SAT seconds")
    p.add_argument("--include-at-risk", action="store_true",
                   help="also rebook AT_RISK parcels now (off: the benchmark found it doesn't pay)")
    a = p.parse_args(argv)

    view = OperatorView.from_csv_dir(a.scenario)
    dets = detect(view, DetectorConfig(grace_h=a.grace))
    if not a.include_at_risk:
        dets = [d for d in dets if d.kind != KIND_AT_RISK]
    cfg = OptionsConfig()
    options = generate_options(view, dets, cfg)
    cap = leg_capacity(view)
    print(f"{len(dets)} flagged shipments, {sum(map(len, options.values()))} options")

    has_truth = (a.scenario / "sim_truth.csv").exists() and (a.scenario / "sim_trip_delays.csv").exists()
    if has_truth:
        truth = read_table(a.scenario, "sim_truth")
        delays = {d.trip_id: d.delay_h for d in read_table(a.scenario, "sim_trip_delays")}

    rows = []
    for name in (SOLVERS if a.solver == "all" else [a.solver]):
        solve = SOLVERS[name]
        plan = solve(options, cap, time_limit_s=a.time_limit) if name == "cpsat" else solve(options, cap)
        write_plan(plan, a.scenario / f"plan_{name}.csv")
        row = {"solver": name, "objective_inr": round(plan.objective_inr), "solve_s": plan.solve_s}
        if has_truth:
            row.update(replay(view, truth, delays, dets, plan, cfg)[1])
        rows.append(row)
    print_table(rows)
    print(f"wrote plan_*.csv to {a.scenario}")


if __name__ == "__main__":
    main()
