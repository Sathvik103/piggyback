"""Cross-checks between the simulator's CSVs, db/schema.sql and db/load.sql.

These cannot replace loading into a real Postgres, but they catch the usual
drift: a renamed field, a reordered column, a table the loader forgot.
"""

import csv
import json
import re
from pathlib import Path

import pytest

from simulator import cli
from simulator.export import TABLES, write_all
from simulator.models import column_names

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
LOADER = (ROOT / "db" / "load.sql").read_text(encoding="utf-8")
ALL_TABLES = ["scenario_meta", *TABLES]


def schema_columns() -> dict[str, list[str]]:
    out = {}
    for name, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", SCHEMA, re.S):
        cols = []
        for line in body.splitlines():
            word = line.strip().split(" ", 1)[0]
            if re.fullmatch(r"[a-z_][a-z0-9_]*", word):  # skips CHECK/UNIQUE, comments, continuations
                cols.append(word)
        out[name] = cols
    return out


def loader_columns() -> dict[str, list[str]]:
    return {t: [c.strip() for c in cols.split(",")]
            for t, cols in re.findall(r"^\\copy (\w+) \(([^)]*)\) FROM '\1\.csv'", LOADER, re.M)}


@pytest.fixture(scope="module")
def out_dir(scenario, tmp_path_factory):
    d = tmp_path_factory.mktemp("scenario")
    write_all(scenario, d)
    return d


def test_schema_has_every_table_with_csv_columns_first_in_order():
    schema = schema_columns()
    assert set(schema) == set(ALL_TABLES)
    for table, (cls, _) in TABLES.items():
        cols = column_names(cls)
        assert schema[table][:len(cols)] == cols, table
        # Anything after the CSV columns is derived in load.sql (PostGIS geometry).
        assert all(c == "geom" for c in schema[table][len(cols):]), table


def test_loader_copies_every_table_with_matching_columns_in_fk_order():
    loaded = loader_columns()
    assert list(loaded) == ALL_TABLES  # TABLES is ordered so references load first
    for table, (cls, _) in TABLES.items():
        assert loaded[table] == column_names(cls)
    assert re.search(r"TRUNCATE " + r",\s*".join(ALL_TABLES) + ";", LOADER)


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


def test_load_script_targets_mounted_paths():
    script = (ROOT / "scripts" / "load_db.sh").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./data:/data" in compose and "./db:/db" in compose
    assert "/docker-entrypoint-initdb.d/" in compose
    assert "-f /db/load.sql" in script and "/data/" in script
