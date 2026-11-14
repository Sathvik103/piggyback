"""Postgres repository: scenarios, the operator view, plans and decisions.

The API talks to Postgres only through this class. Writes that can conflict
(accepting a recovery onto shared spare capacity) lock the legs involved, in
leg_id order, so concurrent approvals serialise instead of overbooking a truck.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from collections import defaultdict
from dataclasses import asdict, fields

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from detection.view import OperatorView
from recovery.optimizer import Plan
from recovery.options import RecoveryOption
from simulator.export import TABLES, TRUTH_TABLES, summary
from simulator.models import Scenario, TripDelay, TruthRecord, column_names

from .passwords import hash_password, verify_dummy, verify_password

DEFAULT_URL = os.environ.get("DATABASE_URL", "postgresql://piggyback:piggyback@localhost:5433/piggyback")

ACCEPT, DISMISS, REVOKE = "ACCEPT", "DISMISS", "REVOKE"
ROLES = ("operator", "viewer")
SESSION_HOURS = 12
_OPTION_COLS = [f.name for f in fields(RecoveryOption)]


class CapacityConflict(Exception):
    """Accepting this option would overbook one or more legs."""

    def __init__(self, legs: list[str]):
        super().__init__(f"not enough spare capacity on {', '.join(legs)}")
        self.legs = legs


class Repo:
    def __init__(self, conninfo: str = DEFAULT_URL, min_size: int = 1, max_size: int = 8):
        self.pool = ConnectionPool(conninfo, min_size=min_size, max_size=max_size, open=True)

    def close(self) -> None:
        self.pool.close()

    # ------------------------------------------------------------------ scenarios

    def scenarios(self) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT scenario_id, summary FROM scenarios ORDER BY scenario_id").fetchall()
        return [{"scenario_id": sid, **s} for sid, s in rows]

    def scenario(self, sid: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT summary FROM scenarios WHERE scenario_id = %s", (sid,)).fetchone()
        return None if row is None else {"scenario_id": sid, **row[0]}

    def exists(self, sid: str) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("SELECT 1 FROM scenarios WHERE scenario_id = %s", (sid,)).fetchone() is not None

    def save_scenario(self, sid: str, sc: Scenario) -> dict:
        """Insert a whole scenario in one transaction (COPY per table)."""
        s = summary(sc)
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO scenarios (scenario_id, seed, start_at, now_h, horizon_h, config, summary) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (sid, sc.config.seed, sc.config.start_at, sc.config.now_h, sc.config.horizon_h,
                 Jsonb(asdict(sc.config)), Jsonb(s)))
            with conn.cursor() as cur:
                for table, (cls, attr) in TABLES.items():
                    cols = column_names(cls)
                    with cur.copy(f"COPY {table} (scenario_id, {', '.join(cols)}) FROM STDIN") as cp:
                        for row in getattr(sc, attr):
                            cp.write_row([sid, *(getattr(row, c) for c in cols)])
            conn.execute("UPDATE hubs SET geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography "
                         "WHERE scenario_id = %s", (sid,))
            conn.execute("""UPDATE lanes l
                            SET geom = ST_MakeLine(f.geom::geometry, t.geom::geometry)::geography
                            FROM hubs f, hubs t
                            WHERE l.scenario_id = %s AND f.scenario_id = l.scenario_id AND t.scenario_id = l.scenario_id
                              AND f.hub_id = l.from_hub AND t.hub_id = l.to_hub""", (sid,))
        return s

    def delete_scenario(self, sid: str) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM scenarios WHERE scenario_id = %s", (sid,)).rowcount > 0

    def view(self, sid: str) -> OperatorView | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT now_h FROM scenarios WHERE scenario_id = %s", (sid,)).fetchone()
            if row is None:
                return None
            data = {}
            for table, (cls, attr) in TABLES.items():
                if table in TRUTH_TABLES:
                    continue
                cols = column_names(cls)
                rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE scenario_id = %s "
                                    f"ORDER BY {cols[0]}", (sid,)).fetchall()
                data[attr] = [cls(*r) for r in rows]
        return OperatorView(row[0], data["hubs"], data["lanes"], data["corridors"], data["trips"],
                            data["legs"], data["shipments"], data["scans"], data["disruptions"])

    def truth(self, sid: str) -> tuple[list[TruthRecord], dict[str, TripDelay]]:
        cols = column_names(TruthRecord)
        dcols = column_names(TripDelay)
        with self.pool.connection() as conn:
            truth = [TruthRecord(*r) for r in conn.execute(
                f"SELECT {', '.join(cols)} FROM sim_truth WHERE scenario_id = %s ORDER BY shipment_id", (sid,))]
            delays = {r[0]: TripDelay(*r) for r in conn.execute(
                f"SELECT {', '.join(dcols)} FROM sim_trip_delays WHERE scenario_id = %s", (sid,))}
        return truth, delays

    # ------------------------------------------------------------------ capacity

    def holds(self, sid: str) -> dict[str, tuple[float, float]]:
        out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        with self.pool.connection() as conn:
            for leg, kg, m3 in conn.execute(
                    "SELECT leg_id, sum(kg), sum(m3) FROM capacity_holds WHERE scenario_id = %s GROUP BY leg_id",
                    (sid,)):
                out[leg] = [kg, m3]
        return {k: (v[0], v[1]) for k, v in out.items()}

    def residual_capacity(self, sid: str, view: OperatorView) -> dict[str, tuple[float, float]]:
        """Spare capacity per leg minus what accepted recoveries already hold."""
        held = self.holds(sid)
        return {l.leg_id: (l.spare_kg - held.get(l.leg_id, (0, 0))[0],
                           l.spare_m3 - held.get(l.leg_id, (0, 0))[1]) for l in view.legs}

    # ------------------------------------------------------------------ plans

    def save_plan(self, sid: str, plan: Plan, grace_h: float, options_considered: int) -> int:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('plans:' || %s))", (sid,))
            plan_no = conn.execute("SELECT coalesce(max(plan_no), 0) + 1 FROM plans WHERE scenario_id = %s",
                                   (sid,)).fetchone()[0]
            conn.execute(
                "INSERT INTO plans (scenario_id, plan_no, solver, grace_h, status, objective_inr, solve_s, stats, "
                "options_considered) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (sid, plan_no, plan.solver, grace_h, plan.status, plan.objective_inr, plan.solve_s,
                 Jsonb(plan.stats), options_considered))
            with conn.cursor() as cur, cur.copy(
                    f"COPY plan_choices (scenario_id, plan_no, {', '.join(_OPTION_COLS)}) FROM STDIN") as cp:
                for o in sorted(plan.chosen.values(), key=lambda o: o.shipment_id):
                    cp.write_row([sid, plan_no, *(list(v) if isinstance(v, tuple) else v
                                                  for v in (getattr(o, c) for c in _OPTION_COLS))])
        return plan_no

    def plans(self, sid: str) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            plans = cur.execute(
                "SELECT plan_no, solver, grace_h, status, objective_inr, solve_s, stats, options_considered, "
                "created_at FROM plans WHERE scenario_id = %s ORDER BY plan_no", (sid,)).fetchall()
            by_plan: dict[int, list[dict]] = defaultdict(list)
            for r in cur.execute(
                    "SELECT plan_no, mode, count(*) AS n, sum(cost_inr) AS cost FROM plan_choices "
                    "WHERE scenario_id = %s GROUP BY plan_no, mode ORDER BY plan_no, mode", (sid,)).fetchall():
                by_plan[r["plan_no"]].append(r)
        for p in plans:
            rows = by_plan[p["plan_no"]]
            p["created_at"] = p["created_at"].isoformat()
            p["shipments"] = sum(r["n"] for r in rows)
            p["cost_inr"] = round(sum(r["cost"] for r in rows))
            p["by_mode"] = {r["mode"]: r["n"] for r in rows}
        return plans

    def plan(self, sid: str, plan_no: int) -> dict | None:
        for p in self.plans(sid):
            if p["plan_no"] == plan_no:
                p["choices"] = self.plan_choices(sid, plan_no)
                return p
        return None

    def plan_choices(self, sid: str, plan_no: int, shipment_id: str | None = None) -> list[RecoveryOption]:
        q = f"SELECT {', '.join(_OPTION_COLS)} FROM plan_choices WHERE scenario_id = %s AND plan_no = %s"
        args: list = [sid, plan_no]
        if shipment_id is not None:
            q += " AND shipment_id = %s"
            args.append(shipment_id)
        with self.pool.connection() as conn:
            rows = conn.execute(q + " ORDER BY shipment_id", args).fetchall()
        return [_option(r) for r in rows]

    # ------------------------------------------------------------------ users and sessions

    def add_user(self, username: str, display_name: str, role: str, password: str) -> None:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO users (username, display_name, role, password_hash) VALUES (%s, %s, %s, %s)",
                         (username, display_name, role, hash_password(password)))

    def set_password(self, username: str, password: str) -> bool:
        """Also signs the user out everywhere."""
        with self.pool.connection() as conn, conn.transaction():
            n = conn.execute("UPDATE users SET password_hash = %s WHERE username = %s",
                             (hash_password(password), username)).rowcount
            conn.execute("DELETE FROM sessions WHERE username = %s", (username,))
        return n > 0

    def set_user(self, username: str, *, role: str | None = None, disabled: bool | None = None,
                 display_name: str | None = None) -> bool:
        if role is not None and role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        with self.pool.connection() as conn, conn.transaction():
            n = conn.execute(
                "UPDATE users SET role = coalesce(%s, role), disabled = coalesce(%s, disabled), "
                "display_name = coalesce(%s, display_name) WHERE username = %s",
                (role, disabled, display_name, username)).rowcount
            if disabled:
                conn.execute("DELETE FROM sessions WHERE username = %s", (username,))
        return n > 0

    def remove_user(self, username: str) -> bool:
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM users WHERE username = %s", (username,)).rowcount > 0

    def users(self) -> list[dict]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            return cur.execute("SELECT username, display_name, role, disabled, created_at FROM users "
                               "ORDER BY username").fetchall()

    def authenticate(self, username: str, password: str) -> dict | None:
        """The user if the password is right and the account is enabled. Takes about as
        long for an unknown username as for a wrong password."""
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute("SELECT username, display_name, role, password_hash, disabled FROM users "
                              "WHERE username = %s", (username,)).fetchone()
        if row is None:
            return verify_dummy(password) or None
        if not verify_password(password, row["password_hash"]) or row["disabled"]:
            return None
        return {k: row[k] for k in ("username", "display_name", "role")}

    def create_session(self, username: str, hours: float = SESSION_HOURS) -> str:
        """A new random token; only its hash is stored."""
        token = secrets.token_urlsafe(32)
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < now()")
            conn.execute("INSERT INTO sessions (token_sha256, username, expires_at) "
                         "VALUES (%s, %s, now() + make_interval(secs => %s))",
                         (_token_hash(token), username, hours * 3600))
        return token

    def session_user(self, token: str) -> dict | None:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(
                "SELECT u.username, u.display_name, u.role FROM sessions s JOIN users u USING (username) "
                "WHERE s.token_sha256 = %s AND s.expires_at > now() AND NOT u.disabled",
                (_token_hash(token),)).fetchone()

    def end_session(self, token: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM sessions WHERE token_sha256 = %s", (_token_hash(token),))

    # ------------------------------------------------------------------ decisions

    def decide(self, sid: str, shipment_id: str, action: str, option: RecoveryOption | None = None,
               plan_no: int | None = None, note: str = "", decided_by: str = "operator") -> dict:
        if (action == ACCEPT) != (option is not None):
            raise ValueError("ACCEPT needs an option; DISMISS and REVOKE take none")
        with self.pool.connection() as conn, conn.transaction():
            # One decision at a time per shipment.
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s || ':' || %s))", (sid, shipment_id))
            current = conn.execute(
                "SELECT action FROM current_decisions WHERE scenario_id = %s AND shipment_id = %s",
                (sid, shipment_id)).fetchone()
            if action == REVOKE and (current is None or current[0] == REVOKE):
                raise LookupError(f"{shipment_id} has no decision in force to revoke")
            if option is not None and option.capacity_legs:
                legs = sorted(set(option.capacity_legs))
                # Lock the legs (in a fixed order, so concurrent approvals can't deadlock).
                spare = {l: (kg, m3) for l, kg, m3 in conn.execute(
                    "SELECT leg_id, spare_kg, spare_m3 FROM trip_legs WHERE scenario_id = %s AND leg_id = ANY(%s) "
                    "ORDER BY leg_id FOR UPDATE", (sid, legs))}
                held = {l: (kg, m3) for l, kg, m3 in conn.execute(
                    "SELECT leg_id, sum(kg), sum(m3) FROM capacity_holds "
                    "WHERE scenario_id = %s AND leg_id = ANY(%s) AND shipment_id <> %s GROUP BY leg_id",
                    (sid, legs, shipment_id))}
                short = [l for l in legs
                         if spare[l][0] - held.get(l, (0, 0))[0] < option.weight_kg - 1e-9
                         or spare[l][1] - held.get(l, (0, 0))[1] < option.volume_m3 - 1e-9]
                if short:
                    raise CapacityConflict(short)
            row = conn.execute(
                "INSERT INTO decisions (scenario_id, shipment_id, action, plan_no, option, note, decided_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING decision_id, decided_at",
                (sid, shipment_id, action, plan_no, Jsonb(asdict(option)) if option else None, note,
                 decided_by)).fetchone()
        return {"decision_id": row[0], "decided_at": row[1].isoformat(), "scenario_id": sid,
                "shipment_id": shipment_id, "action": action, "plan_no": plan_no,
                "option": asdict(option) if option else None, "note": note, "decided_by": decided_by}

    def decisions(self, sid: str, in_force_only: bool = True) -> list[dict]:
        src = "current_decisions" if in_force_only else "decisions"
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                f"SELECT decision_id, shipment_id, action, plan_no, option, note, decided_by, decided_at "
                f"FROM {src} WHERE scenario_id = %s {'AND action <> %s' if in_force_only else ''} "
                f"ORDER BY decision_id", (sid, REVOKE) if in_force_only else (sid,)).fetchall()
        for r in rows:
            r["decided_at"] = r["decided_at"].isoformat()
        return rows


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _option(row) -> RecoveryOption:
    d = dict(zip(_OPTION_COLS, row, strict=True))
    d["legs"], d["capacity_legs"] = tuple(d["legs"]), tuple(d["capacity_legs"])
    return RecoveryOption(**d)


def option_from_json(d: dict) -> RecoveryOption:
    d = {**d, "legs": tuple(d["legs"]), "capacity_legs": tuple(d["capacity_legs"])}
    return RecoveryOption(**{k: d[k] for k in _OPTION_COLS})
