"""Nova's icon, drawn with Pillow so there are no image assets to keep in sync.

    python -m assistant.ui.icon     # regenerates assistant/ui/nova.ico (used by the .exe build)
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


def make_image(state: str = "idle", size: int = 64, muted: bool = False) -> Image.Image:
    scale = 4  # draw large, downsample for smooth edges
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2 * scale, 2 * scale, s - 2 * scale, s - 2 * scale), fill=(28, 28, 26, 255))
    color = STATUS_COLORS.get("muted" if muted else state, STATUS_COLORS["idle"])
    ring = max(3, s // 12)
    draw.ellipse((ring, ring, s - ring, s - ring), outline=color + (255,), width=ring)
    # A four-point "spark" in the middle.
    c, r, w = s / 2, s * 0.26, s * 0.075
    points = [(c, c - r), (c + w, c - w), (c + r, c), (c + w, c + w), (c, c + r), (c - w, c + w), (c - r, c), (c - w, c - w)]
    draw.polygon(points, fill=(245, 244, 240, 255))
    if muted:
        draw.line((s * 0.22, s * 0.78, s * 0.78, s * 0.22), fill=(239, 68, 68, 255), width=max(4, s // 14))
    return image.resize((size, size), Image.LANCZOS)


def write_ico(path: Path = ICO_PATH) -> Path:
    make_image("listening", 256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    return path


if __name__ == "__main__":
    print(write_ico())
