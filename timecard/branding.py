"""Company branding: logo image and brand color.

The logo is normalized to a PNG (at most 800 x 400 px) and stored inside the workbook on a hidden "Branding"
sheet by storage.py, so it travels with the workbook, its backups and portable copies.
"""
from __future__ import annotations

import io
import re

from PIL import Image, UnidentifiedImageError

MAX_LOGO_SIZE = (800, 400)
MAX_INPUT_BYTES = 20 * 1024 * 1024
DEFAULT_BRAND_COLOR = "#1F3A5F"
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def normalize_logo(data: bytes) -> bytes:
    """Any common image (PNG, JPG, BMP, GIF) -> PNG bytes no larger than MAX_LOGO_SIZE.

    Raises ValueError if the data isn't a readable image.
    """
    if not data:
        raise ValueError("The logo file is empty.")
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("The logo file is too large (over 20 MB). Choose a smaller image.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("That file isn't a picture TimeBooks can read. Use a PNG, JPG or BMP.") from exc
    img = img.convert("RGBA")
    bbox = img.getbbox()  # trim fully transparent borders
    if bbox:
        img = img.crop(bbox)
    img.thumbnail(MAX_LOGO_SIZE, Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def logo_size(png: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(png)) as img:
        return img.size


def fit_logo(png: bytes, max_w: float, max_h: float) -> tuple[float, float]:
    """Width/height that fit the logo inside max_w x max_h while keeping its shape."""
    w, h = logo_size(png)
    scale = min(max_w / w, max_h / h)
    return w * scale, h * scale


def is_valid_color(color: str) -> bool:
    return bool(_HEX.match(color or ""))


def brand_color(color: str) -> str:
    """The color if it is a valid #RRGGBB, else the default."""
    return color.upper() if is_valid_color(color) else DEFAULT_BRAND_COLOR


def _luminance(color: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((_luminance(brand_color(a)), _luminance(brand_color(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_on(color: str) -> str:
    """Black or white, whichever reads better on `color`."""
    color = brand_color(color)
    return "#FFFFFF" if contrast_ratio(color, "#FFFFFF") >= contrast_ratio(color, "#000000") else "#000000"


def ink_color(color: str) -> str:
    """The brand color if it is dark enough to read as text on white paper (4.5:1), else near-black."""
    color = brand_color(color)
    return color if contrast_ratio(color, "#FFFFFF") >= 4.5 else "#222222"
