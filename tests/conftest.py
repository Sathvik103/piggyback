import os

import psycopg
import pytest
from psycopg import sql

from simulator import generate_scenario

TEST_DB_URL = os.environ.get("PIGGYBACK_TEST_DATABASE_URL",
                             "postgresql://piggyback:piggyback@localhost:5433/piggyback_test")


@pytest.fixture(scope="session")
def scenario():
    return generate_scenario(seed=42)


@pytest.fixture(scope="session")
def pg_url():
    """A fresh, migrated test database. Skips if Postgres isn't reachable
    (start it with `docker compose up -d db`)."""
    info = psycopg.conninfo.conninfo_to_dict(TEST_DB_URL)
    admin = psycopg.conninfo.make_conninfo(TEST_DB_URL, dbname="postgres")
    try:
        with psycopg.connect(admin, autocommit=True, connect_timeout=3) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (info["dbname"],)).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(info["dbname"])))
    except psycopg.OperationalError as e:
        if os.environ.get("PIGGYBACK_REQUIRE_DB") == "1":  # CI: a missing DB is a failure, not a skip
            pytest.fail(f"Postgres not reachable at {TEST_DB_URL}: {e}")
        pytest.skip(f"Postgres not reachable at {TEST_DB_URL}: {e}")
    with psycopg.connect(TEST_DB_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    from db import migrate
    migrate(TEST_DB_URL)
    return TEST_DB_URL


@pytest.fixture(scope="session")
def make_user(pg_url):
    """make_user(name, role="operator") creates (or resets) a test user with a fresh
    random password and returns the password."""
    import secrets

    from db import Repo

    def make(username: str, role: str = "operator") -> str:
        password = secrets.token_urlsafe(16)
        r = Repo(pg_url, max_size=1)
        try:
            r.remove_user(username)
            r.add_user(username, username.title(), role, password)
        finally:
            r.close()
        return password
    return make
