"""Fetch real road distances and drive times between the hubs from OSRM.

    python scripts/fetch_road_matrix.py            # writes simulator/data/road_matrix.json

Uses the public OSRM demo server (OpenStreetMap data, car profile) with one table
request for all hub pairs. The result is committed, so the simulator never needs the
network; re-run this only when the hub list changes. Data (c) OpenStreetMap
contributors, ODbL.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.hubs import HUBS  # noqa: E402

OSRM = "https://router.project-osrm.org/table/v1/driving/"
OUT = ROOT / "simulator" / "data" / "road_matrix.json"


def main() -> None:
    coords = ";".join(f"{h.lon:.4f},{h.lat:.4f}" for h in HUBS)
    req = urllib.request.Request(f"{OSRM}{coords}?annotations=distance,duration",
                                 headers={"User-Agent": "piggyback-road-matrix/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    if data.get("code") != "Ok":
        raise SystemExit(f"OSRM error: {data.get('code')} {data.get('message', '')}")
    codes = [h.hub_id for h in HUBS]
    km = {a: {b: round(data["distances"][i][j] / 1000, 1) for j, b in enumerate(codes)}
          for i, a in enumerate(codes)}
    car_h = {a: {b: round(data["durations"][i][j] / 3600, 2) for j, b in enumerate(codes)}
             for i, a in enumerate(codes)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "source": "OSRM table service, car profile, OpenStreetMap data (c) OpenStreetMap contributors, ODbL",
        "fetched": date.today().isoformat(),
        "hubs": codes,
        "road_km": km,
        "car_hours": car_h,
    }, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(codes)} hubs)")


if __name__ == "__main__":
    main()
