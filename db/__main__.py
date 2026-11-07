"""Database admin CLI.

    python -m db migrate                         # apply pending migrations
    python -m db seed scenario_42 --seed 42      # generate a scenario straight into Postgres
    python -m db load data/scenario_42 [--id x]  # load a simulator.cli output folder
    python -m db drop scenario_42                # delete a scenario and everything under it

Uses $DATABASE_URL (default: the docker compose database).
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import fields
from pathlib import Path

from detection.view import read_table
from simulator import generate_scenario
from simulator.export import TABLES
from simulator.models import Scenario, ScenarioConfig

from .migrate import migrate
from .repo import DEFAULT_URL, Repo


def scenario_from_dir(path: Path) -> Scenario:
    """Rebuild a full Scenario (truth included) from a simulator.cli output folder."""
    with open(path / "scenario_meta.csv", newline="", encoding="utf-8") as f:
        meta = {r["key"]: r["value"] for r in csv.DictReader(f)}
    types = {f.name: f.type for f in fields(ScenarioConfig)}
    cast = {"int": int, "float": float, "str": str}
    cfg = ScenarioConfig(**{k: cast[types[k]](v) for k, v in meta.items() if k in types})
    t = {name: read_table(path, name) for name in TABLES}
    return Scenario(cfg, t["hubs"], t["lanes"], t["corridors"], t["trips"], t["trip_legs"],
                    t["shipments"], t["scan_events"], t["disruptions"], t["sim_truth"], t["sim_trip_delays"])


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m db", description=__doc__.split("\n\n")[0])
    p.add_argument("--url", default=DEFAULT_URL, help="Postgres connection string")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    s = sub.add_parser("seed")
    s.add_argument("scenario_id")
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--n-shipments", type=int, default=ScenarioConfig().n_shipments)
    s.add_argument("--capacity-tightness", type=float, default=ScenarioConfig().capacity_tightness)
    ld = sub.add_parser("load")
    ld.add_argument("path", type=Path)
    ld.add_argument("--id", dest="scenario_id")
    dr = sub.add_parser("drop")
    dr.add_argument("scenario_id")
    a = p.parse_args(argv)

    applied = migrate(a.url)
    if a.cmd == "migrate":
        print(f"applied migrations: {applied or 'none (up to date)'}")
        return
    repo = Repo(a.url, max_size=2)
    try:
        if a.cmd == "seed":
            sc = generate_scenario(seed=a.seed, n_shipments=a.n_shipments,
                                   capacity_tightness=a.capacity_tightness)
            print(json.dumps(repo.save_scenario(a.scenario_id, sc), indent=2))
        elif a.cmd == "load":
            sid = a.scenario_id or a.path.name
            print(json.dumps(repo.save_scenario(sid, scenario_from_dir(a.path)), indent=2))
        elif a.cmd == "drop":
            print("dropped" if repo.delete_scenario(a.scenario_id) else "no such scenario")
    finally:
        repo.close()


if __name__ == "__main__":
    main()
