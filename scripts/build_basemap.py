"""Build the web app's base map from Natural Earth (public domain).

    # download once (any folder), then:
    python scripts/build_basemap.py ne_10m_admin_0_countries_ind.geojson ne_10m_admin_1_states_provinces_lines.geojson

Source files: https://github.com/nvkelso/natural-earth-vector (geojson/). The country
file is Natural Earth's India point-of-view edition, so India's outline follows the
boundaries shown on the official map of India. Writes web/public/basemap/india.json:
India (land), its neighbours (clipped to the region), and India's state boundaries,
simplified for a network-scale map. Needs `shapely` (only for this script).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from shapely.geometry import box, mapping, shape

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public" / "basemap" / "india.json"
REGION = box(60.0, 2.0, 103.0, 40.0)    # lon/lat window the map can show
TOL_INDIA, TOL_OTHER, TOL_LINES = 0.012, 0.03, 0.015  # degrees (~1.3 km / 3 km / 1.7 km)


def _round(obj, nd=3):
    if isinstance(obj, (list, tuple)):
        return [_round(x, nd) for x in obj]
    return round(obj, nd) if isinstance(obj, float) else obj


def _feature(geom, **props):
    m = mapping(geom)
    geometry = {"type": m["type"], "coordinates": _round(m["coordinates"])}
    return {"type": "Feature", "properties": props, "geometry": geometry}


def build(countries_path: Path, lines_path: Path) -> dict:
    countries = json.loads(countries_path.read_text(encoding="utf-8"))["features"]
    lines = json.loads(lines_path.read_text(encoding="utf-8"))["features"]
    out = []
    for f in countries:
        g = shape(f["geometry"])
        if not g.intersects(REGION):
            continue
        is_india = f["properties"]["ADM0_A3"] == "IND"
        g = g.intersection(REGION).simplify(TOL_INDIA if is_india else TOL_OTHER, preserve_topology=True)
        if not g.is_empty:
            out.append(_feature(g, kind="india" if is_india else "neighbour", name=f["properties"]["NAME"]))
    for f in lines:
        if f["properties"].get("ADM0_A3") != "IND":
            continue
        g = shape(f["geometry"]).simplify(TOL_LINES, preserve_topology=True)
        if not g.is_empty:
            out.append(_feature(g, kind="state-line"))
    return {"type": "FeatureCollection",
            "attribution": "Boundaries: Natural Earth (public domain), India point of view",
            "features": out}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    fc = build(Path(sys.argv[1]), Path(sys.argv[2]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    kinds = {}
    for f in fc["features"]:
        kinds[f["properties"]["kind"]] = kinds.get(f["properties"]["kind"], 0) + 1
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB): {kinds}")


if __name__ == "__main__":
    main()
