"""Assemble the frames from `npm run demo` (web/demo-frames) into docs/img/demo.gif.

    cd web && npm run demo && cd .. && python scripts/make_demo_gif.py
"""

import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / "web" / "demo-frames"
OUT = ROOT / "docs" / "img" / "demo.gif"
WIDTH = 1000  # README column width on GitHub is ~900 px; keep it crisp but small


def main() -> None:
    timings = json.loads((FRAMES / "timings.json").read_text(encoding="utf-8"))
    paths = sorted(FRAMES.glob("*.png"))
    if len(paths) != len(timings):
        sys.exit(f"{len(paths)} frames but {len(timings)} timings; re-run `npm run demo`")
    frames = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        im = im.resize((WIDTH, round(im.height * WIDTH / im.width)), Image.LANCZOS)
        # One shared adaptive palette per frame keeps the UI's flat colours exact.
        frames.append(im.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(OUT, save_all=True, append_images=frames[1:], duration=timings, loop=0, optimize=True)
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB, {len(frames)} frames, {sum(timings) / 1000:.1f} s)")


if __name__ == "__main__":
    main()
