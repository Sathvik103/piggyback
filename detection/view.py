"""What an operator can see at `now`. Detection code reads data only through this.

`OperatorView` deliberately has no truth fields, and `from_csv_dir` never opens the
sim_* files, so detection cannot peek at ground truth by accident.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, fields
from pathlib import Path

from simulator.export import TABLES, TRUTH_TABLES
from simulator.models import (
    Corridor,
    Disruption,
    Hub,
    Lane,
    Scenario,
    ScanEvent,
    Shipment,
    Trip,
    TripLeg,
)


@dataclass
class OperatorView:
    now: float
    hubs: list[Hub]
    lanes: list[Lane]
    corridors: list[Corridor]
    trips: list[Trip]
    legs: list[TripLeg]
    shipments: list[Shipment]
    scans: list[ScanEvent]
    disruptions: list[Disruption]

    @classmethod
    def from_scenario(cls, sc: Scenario) -> "OperatorView":
        return cls(sc.now, sc.hubs, sc.lanes, sc.corridors, sc.trips, sc.legs,
                   sc.shipments, sc.scans, sc.disruptions)

    @classmethod
    def from_csv_dir(cls, path: Path) -> "OperatorView":
        path = Path(path)
        with open(path / "scenario_meta.csv", newline="", encoding="utf-8") as f:
            meta = {r["key"]: r["value"] for r in csv.DictReader(f)}
        tables = {name: read_table(path, name) for name in TABLES if name not in TRUTH_TABLES}
        return cls(float(meta["now_h"]), tables["hubs"], tables["lanes"], tables["corridors"],
                   tables["trips"], tables["trip_legs"], tables["shipments"],
                   tables["scan_events"], tables["disruptions"])


_PARSERS = {
    "str": str,
    "int": int,
    "float": float,
    "Optional[str]": lambda v: v or None,
    "Optional[float]": lambda v: float(v) if v else None,
}


def read_table(path: Path, name: str) -> list:
    """Read one exported CSV back into its dataclass rows."""
    cls = TABLES[name][0]
    parse = {f.name: _PARSERS[f.type] for f in fields(cls)}
    with open(Path(path) / f"{name}.csv", newline="", encoding="utf-8") as f:
        return [cls(**{k: parse[k](v) for k, v in row.items()}) for row in csv.DictReader(f)]


def scans_by_shipment(scans: list[ScanEvent]) -> dict[str, list[ScanEvent]]:
    out: dict[str, list[ScanEvent]] = {}
    for e in scans:
        out.setdefault(e.shipment_id, []).append(e)
    for v in out.values():
        v.sort(key=lambda e: (e.ts_h, e.scan_id))
    return out
