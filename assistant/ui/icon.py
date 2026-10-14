"""Nova's icons, built from the N of the owner's logo (assistant/ui/nova-mark.png,
traced by packaging/make_brand.py).

    python -m assistant.ui.icon     # regenerates nova.ico (the .exe) and the web app icons

The tray keeps its status ring, so its colour still says listening / thinking / working;
the N sits inside it. The app icon is the N in paper on a navy tile.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

STATUS_COLORS = {
    "idle": (120, 116, 108),
    "muted": (120, 116, 108),
    "listening": (22, 163, 74),
    "transcribing": (217, 119, 6),
    "thinking": (217, 119, 6),
    "speaking": (79, 70, 229),
    "working": (124, 58, 237),
}
ICO_PATH = Path(__file__).with_name("nova.ico")
MARK_PATH = Path(__file__).with_name("nova-mark.png")
WEB_ICONS = Path(__file__).resolve().parents[2] / "canvas" / "public" / "icons"
NAVY = (15, 52, 115)
PAPER = (245, 245, 242)
_mark: Image.Image | None = None


def _glyph(size: int, colour: tuple[int, int, int]) -> Image.Image:
    """The N at `size` px square, in one colour, on transparent."""
    global _mark
    if _mark is None:
        _mark = Image.open(resource_mark()).convert("RGBA")
    alpha = _mark.getchannel("A").resize((size, size), Image.LANCZOS)
    glyph = Image.new("RGBA", (size, size), colour + (255,))
    glyph.putalpha(alpha)
    return glyph


def resource_mark() -> Path:
    from ..paths import resource_path

    bundled = resource_path("assistant", "ui", "nova-mark.png")
    return bundled if bundled.exists() else MARK_PATH


def make_image(state: str = "idle", size: int = 64, muted: bool = False) -> Image.Image:
    scale = 4  # draw large, downsample for smooth edges
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2 * scale, 2 * scale, s - 2 * scale, s - 2 * scale), fill=(28, 28, 26, 255))
    color = STATUS_COLORS.get("muted" if muted else state, STATUS_COLORS["idle"])
    ring = max(3, s // 12)
    draw.ellipse((ring, ring, s - ring, s - ring), outline=color + (255,), width=ring)
    # The N of the logo in the middle, a touch large: it is italic and leans out of its box.
    inner = int(s * 0.5)
    image.alpha_composite(_glyph(inner, PAPER), ((s - inner) // 2, (s - inner) // 2))
    if muted:
        draw.line((s * 0.22, s * 0.78, s * 0.78, s * 0.22), fill=(239, 68, 68, 255), width=max(4, s // 14))
    return image.resize((size, size), Image.LANCZOS)


def make_app_icon(size: int = 256, safe: float = 0.78) -> Image.Image:
    """The N in paper on a navy square. `safe` is how much of the tile the N may use:
    smaller for maskable web icons, which phones crop to a circle."""
    scale = 4
    s = size * scale
    image = Image.new("RGBA", (s, s), NAVY + (255,))
    inner = int(s * safe)
    image.alpha_composite(_glyph(inner, PAPER), ((s - inner) // 2, (s - inner) // 2))
    return image.resize((size, size), Image.LANCZOS)


def write_ico(path: Path = ICO_PATH) -> Path:
    make_app_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


def write_web_icons(folder: Path = WEB_ICONS) -> list[Path]:
    out = []
    for size in (192, 512):
        for name, safe in ((f"nova-{size}.png", 0.78), (f"nova-maskable-{size}.png", 0.56)):
            make_app_icon(size, safe).convert("RGB").save(folder / name)
            out.append(folder / name)
    return out


if __name__ == "__main__":
    print(write_ico())
    for written in write_web_icons():
        print(written)
