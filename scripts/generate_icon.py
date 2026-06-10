"""Generate a placeholder SFMS ICO build artifact when no real icon exists."""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON_PATH = ROOT / "assets" / "icon.ico"


def _fallback_ico(path: Path) -> None:
    """Write a minimal valid 1x1 ICO if Pillow is unavailable in a test env."""
    width = height = 1
    xor_bitmap = b"\x1a\x1a\x5e\x00"  # BGRA
    and_mask = b"\x00\x00\x00\x00"
    dib = struct.pack("<IIIHHIIIIII", 40, width, height * 2, 1, 32, 0, len(xor_bitmap) + len(and_mask), 0, 0, 0, 0)
    image = dib + xor_bitmap + and_mask
    header = struct.pack("<HHH", 0, 1, 1)
    directory = struct.pack("<BBBBHHII", width, height, 0, 0, 1, 32, len(image), 6 + 16)
    path.write_bytes(header + directory + image)


def generate_icon(path: Path = ICON_PATH) -> Path:
    """Create a simple 256x256 SF placeholder icon if it is missing."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    if importlib.util.find_spec("PIL") is None:
        _fallback_ico(path)
        return path
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (256, 256), "#1a1a5e")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 96)
    except OSError:
        font = ImageFont.load_default()
    text = "SF"
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (256 - (bbox[2] - bbox[0])) // 2
    y = (256 - (bbox[3] - bbox[1])) // 2 - 8
    draw.text((x, y), text, fill="white", font=font)
    image.save(path, format="ICO", sizes=[(256, 256)])
    return path


if __name__ == "__main__":
    print(generate_icon())
