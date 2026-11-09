"""Cross-checks between the simulator's exports and the database migrations.

These run without a database; tests/test_db.py covers a real Postgres. They catch
the usual drift: a renamed field, a reordered column, a table nobody created.
"""

import csv
import json
import re
from pathlib import Path

import pytest

from db.migrate import migrations
from simulator import cli
from simulator.export import TABLES, write_all
from simulator.models import column_names

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "\n".join(p.read_text(encoding="utf-8") for _, p in migrations())


def schema_columns() -> dict[str, list[str]]:
    out = {}
    for name, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", SCHEMA, re.S):
        cols = []
        for line in body.splitlines():
            word = line.strip().split(" ", 1)[0]
            if re.fullmatch(r"[a-z_][a-z0-9_]*", word):  # skips CHECK/UNIQUE/PRIMARY, comments, continuations
                cols.append(word)
        out[name] = cols
    for name, col in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", SCHEMA):
        out[name].append(col)
    return out


@pytest.fixture(scope="module")
def out_dir(scenario, tmp_path_factory):
    d = tmp_path_factory.mktemp("scenario")
    write_all(scenario, d)
    return d


def test_migrations_create_every_simulator_table_with_its_columns_in_order():
    schema = schema_columns()
    assert set(TABLES) <= set(schema)
    for table, (cls, _) in TABLES.items():
        cols = column_names(cls)
        # scenario_id first, then the dataclass fields; derived geometry may sit before
        # columns that later migrations appended.
        assert schema[table][0] == "scenario_id", table
        assert [c for c in schema[table][1:] if c != "geom"] == cols, table
    assert {"scenarios", "plans", "plan_choices", "decisions"} <= set(schema)


def test_plan_choices_store_every_recovery_option_field():
    from recovery.options import RecoveryOption
    assert schema_columns()["plan_choices"][2:] == column_names(RecoveryOption)


def test_csv_headers_and_row_counts(scenario, out_dir):
    for table, (cls, attr) in TABLES.items():
        with open(out_dir / f"{table}.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows[0] == column_names(cls)
        assert len(rows) - 1 == len(getattr(scenario, attr))
    # Optional fields are written as empty strings, which COPY CSV loads as NULL.
    with open(out_dir / "scan_events.csv", newline="", encoding="utf-8") as f:
        first = next(csv.DictReader(f))
    assert first["event_type"] == "INDUCTED" and first["leg_id"] == "" and first["note"] == ""


def test_json_and_summary(scenario, out_dir):
    data = json.loads((out_dir / "scenario.json").read_text(encoding="utf-8"))
    assert data["config"]["seed"] == 42
    assert len(data["shipments"]) == len(scenario.shipments)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["stranded"] == len(scenario.truth)
    assert sum(summary["last_scan_state"].values()) == len(scenario.shipments)


def test_cli_writes_a_scenario(tmp_path, capsys):
    cli.main(["--seed", "5", "--out", str(tmp_path / "s5"), "--n-shipments", "150"])
    assert (tmp_path / "s5" / "shipments.csv").exists()
    assert '"shipments": 150' in capsys.readouterr().out
