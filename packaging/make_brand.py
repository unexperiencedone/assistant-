"""Nova's logo, traced once from the designed PNG into every form the app uses.

    python packaging/make_brand.py [path/to/Nova.png]

The source is the owner's own design (navy script "Nova" with "PERSONAL BUTLER"). It is
traced into vector paths, so every size is crisp and every colour is one fill:

    docs/brand/nova-{lockup,wordmark,mark}-{navy,ink,paper}.svg   files to use anywhere
    docs/brand/nova-logo-source.png                               the original, untouched
    docs/brand/variations.png                                     one sheet of all of them
    canvas/src/brand.js                                           paths for the canvas (currentColor)
    assistant/ui/nova-mark.png                                    the N, white, for the tray and icons

Then run `python -m assistant.ui.icon` for nova.ico and the web app icons.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "docs" / "brand"
NAVY = "#0f3473"   # measured from the source
INK = "#0d0d0d"    # G-pen ink, manga.css --pen
PAPER = "#f5f5f2"  # manuscript paper, manga.css --paper
COLOURS = {"navy": NAVY, "ink": INK, "paper": PAPER}
PAD = 12           # source pixels of breathing room around each shape


def _mask(source: Path) -> np.ndarray:
    grey = cv2.imread(str(source), cv2.IMREAD_GRAYSCALE)
    if grey is None:
        raise SystemExit(f"can't read {source}")
    return (grey < 128).astype(np.uint8)


def _parts(mask: np.ndarray) -> dict[str, np.ndarray]:
    """The N, the "ova" and the tagline, by connected shape: the N is the largest, the
    "ova" the next, and the tagline is the row of small letters beneath them."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    shapes = sorted(range(1, count), key=lambda i: -stats[i, cv2.CC_STAT_AREA])
    n_id, ova_id = shapes[0], shapes[1]
    small = [i for i in shapes[2:] if stats[i, cv2.CC_STAT_AREA] > 30]
    pick = lambda ids: np.isin(labels, ids).astype(np.uint8)  # noqa: E731
    return {"mark": pick([n_id]), "wordmark": pick([n_id, ova_id]), "lockup": pick([n_id, ova_id] + small)}


def _path(mask: np.ndarray) -> tuple[str, str]:
    """An SVG path (even-odd, so holes stay holes) and its viewBox, cropped to the shape."""
    ys, xs = np.nonzero(mask)
    x0, y0 = xs.min() - PAD, ys.min() - PAD
    width, height = xs.max() - xs.min() + 1 + 2 * PAD, ys.max() - ys.min() + 1 + 2 * PAD
    contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    parts = []
    for contour in contours:
        points = cv2.approxPolyDP(contour, 0.7, True).reshape(-1, 2)
        if len(points) < 3:
            continue
        coords = " ".join(f"{x - x0:.0f} {y - y0:.0f}" for x, y in points)
        parts.append(f"M{coords}Z")
    return "".join(parts), f"0 0 {width} {height}"


def _svg(d: str, view_box: str, fill: str, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" role="img">'
            f"<title>{title}</title><path fill=\"{fill}\" fill-rule=\"evenodd\" d=\"{d}\"/></svg>\n")


def _mark_png(mask: np.ndarray, size: int = 512) -> Image.Image:
    """The N alone, white on transparent, centred in a square: what the icons draw with."""
    ys, xs = np.nonzero(mask)
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1] * 255
    glyph = Image.fromarray(crop.astype(np.uint8), "L")
    scale = size * 0.92 / max(glyph.size)
    glyph = glyph.resize((round(glyph.width * scale), round(glyph.height * scale)), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    white = Image.new("RGBA", glyph.size, (255, 255, 255, 255))
    out.paste(white, ((size - glyph.width) // 2, (size - glyph.height) // 2), glyph)
    return out


def _sheet(masks: dict[str, np.ndarray]) -> Image.Image:
    """Every variant on its intended ground, for choosing at a glance."""
    grounds = [("navy", "#ffffff"), ("ink", PAPER), ("paper", INK), ("paper", NAVY)]
    cell_w, cell_h = 560, 300
    sheet = Image.new("RGB", (cell_w * 3, cell_h * len(grounds)), "white")
    for row, (colour, ground) in enumerate(grounds):
        for col, name in enumerate(("lockup", "wordmark", "mark")):
            mask = masks[name]
            ys, xs = np.nonzero(mask)
            crop = Image.fromarray((mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1] * 255).astype(np.uint8), "L")
            scale = min((cell_w - 80) / crop.width, (cell_h - 80) / crop.height)
            crop = crop.resize((round(crop.width * scale), round(crop.height * scale)), Image.LANCZOS)
            cell = Image.new("RGB", (cell_w, cell_h), ground)
            cell.paste(Image.new("RGB", crop.size, COLOURS[colour]),
                       ((cell_w - crop.width) // 2, (cell_h - crop.height) // 2), crop)
            ImageDraw.Draw(cell).rectangle((0, 0, cell_w - 1, cell_h - 1), outline=INK, width=2)
            sheet.paste(cell, (col * cell_w, row * cell_h))
    return sheet


def main(source: Path) -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, BRAND / "nova-logo-source.png")
    masks = _parts(_mask(source))
    paths = {name: _path(mask) for name, mask in masks.items()}
    for name, (d, view_box) in paths.items():
        for colour, fill in COLOURS.items():
            (BRAND / f"nova-{name}-{colour}.svg").write_text(_svg(d, view_box, fill, "Nova"), encoding="utf-8")
    js = ["// Generated by packaging/make_brand.py from docs/brand/nova-logo-source.png. Don't edit by hand.",
          "// Each is one even-odd path; the canvas fills it with currentColor."]
    for name, (d, view_box) in paths.items():
        js.append(f'export const {name.upper()} = {{ viewBox: "{view_box}", d: "{d}" }};')
    (ROOT / "canvas" / "src" / "brand.js").write_text("\n".join(js) + "\n", encoding="utf-8")
    _mark_png(masks["mark"]).save(ROOT / "assistant" / "ui" / "nova-mark.png")
    _sheet(masks).save(BRAND / "variations.png")
    for name, (d, _) in paths.items():
        print(f"{name}: {len(d) // 1024} KB of path")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "Downloads" / "Nova.png")
