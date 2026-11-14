"""Database admin CLI.

    python -m db migrate                         # apply pending migrations
    python -m db seed scenario_42 --seed 42      # generate a scenario straight into Postgres
    python -m db load data/scenario_42 [--id x]  # load a simulator.cli output folder
    python -m db drop scenario_42                # delete a scenario and everything under it

    python -m db user add asha --role operator --name "Asha R."   # prompts for the password
    python -m db user passwd asha                                  # also signs them out everywhere
    python -m db user list | disable asha | enable asha | remove asha
    (--password-stdin reads the password from stdin instead, for scripts)

Uses $DATABASE_URL (default: the docker compose database).
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import sys
from dataclasses import fields
from pathlib import Path

import psycopg

from detection.view import read_table
from simulator import generate_scenario
from simulator.export import TABLES
from simulator.models import Scenario, ScenarioConfig

from .migrate import migrate
from .repo import DEFAULT_URL, ROLES, Repo


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
    u = sub.add_parser("user", help="manage operator accounts")
    u.add_argument("action", choices=["add", "passwd", "list", "disable", "enable", "remove"])
    u.add_argument("username", nargs="?")
    u.add_argument("--role", choices=ROLES, default="operator")
    u.add_argument("--name", dest="display_name", help="display name (default: the username)")
    u.add_argument("--password-stdin", action="store_true", help="read the password from stdin")
    a = p.parse_args(argv)
    if a.cmd == "user" and a.action != "list" and not a.username:
        p.error(f"user {a.action} needs a username")

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
        elif a.cmd == "user":
            _user(repo, a)
    finally:
        repo.close()


def _password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Password: ")
    if pw != getpass.getpass("Again: "):
        raise SystemExit("passwords don't match")
    return pw


def _user(repo: Repo, a: argparse.Namespace) -> None:
    try:
        if a.action == "list":
            for r in repo.users():
                print(f"{r['username']:<20} {r['role']:<9} {'disabled' if r['disabled'] else 'active':<9} "
                      f"{r['display_name']}")
        elif a.action == "add":
            repo.add_user(a.username, a.display_name or a.username, a.role, _password(a.password_stdin))
            print(f"added {a.username} ({a.role})")
        elif a.action == "passwd":
            ok = repo.set_password(a.username, _password(a.password_stdin))
            print("password changed; signed out everywhere" if ok else "no such user")
        elif a.action in ("disable", "enable"):
            ok = repo.set_user(a.username, disabled=a.action == "disable")
            print(f"{a.action}d" if ok else "no such user")
        elif a.action == "remove":
            print("removed" if repo.remove_user(a.username) else "no such user")
    except ValueError as e:
        raise SystemExit(str(e)) from None
    except psycopg.errors.UniqueViolation:
        raise SystemExit(f"{a.username} already exists") from None
    except psycopg.errors.CheckViolation:
        raise SystemExit("usernames are 2-32 characters: lowercase letters, digits, '.', '_' or '-'") from None


if __name__ == "__main__":
    main()
