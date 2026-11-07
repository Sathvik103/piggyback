"""Apply db/migrations/NNN_*.sql in order, each once, each in its own transaction."""

from __future__ import annotations

import re
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def migrations() -> list[tuple[int, Path]]:
    out = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _NAME.match(p.name)
        if not m:
            raise ValueError(f"bad migration file name: {p.name}")
        out.append((int(m.group(1)), p))
    versions = [v for v, _ in out]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(f"migration versions must be 1..n without gaps, got {versions}")
    return out


def migrate(conninfo: str) -> list[int]:
    """Bring the database up to date; returns the versions applied now."""
    applied_now = []
    with psycopg.connect(conninfo, autocommit=True) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                            version integer PRIMARY KEY,
                            applied_at timestamptz NOT NULL DEFAULT now())""")
        # One migrator at a time (e.g. several API workers starting together).
        conn.execute("SELECT pg_advisory_lock(hashtext('piggyback_migrate'))")
        try:
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for version, path in migrations():
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
                applied_now.append(version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext('piggyback_migrate'))")
    return applied_now
