#!/usr/bin/env python3
"""Generate the brand icons under custom_components/njtransit/brand/.

Home Assistant serves local brand images for custom integrations straight from
that directory, so no pull request against home-assistant/brands is needed.

The artwork is deliberately **original**. NJ Transit's own logo and wordmark
are their trademarks, and this integration is unaffiliated -- shipping their
mark would contradict the disclaimer in the README. This is a generic
commuter-rail glyph that identifies what the integration talks to without
imitating anyone's identity. For the same reason there is no logo.png: a logo
is exactly where the temptation to set "NJ Transit" in their typeface lives,
and Home Assistant falls back to the icon perfectly well without one.

Run after changing anything here; the PNGs are committed:

    uv run python scripts/generate_brand.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

BRAND_DIR = Path(__file__).parent.parent / "custom_components" / "njtransit" / "brand"

# Everything is drawn at 4x and downsampled, which is cheaper than antialiasing
# by hand and gives clean curves at both output sizes.
CANVAS = 2048
SIZES = (512, 256)

# Deliberately not NJ Transit's palette. A muted slate blue reads as rail
# without evoking their orange.
LIGHT_THEME_GLYPH = (27, 58, 92, 255)
DARK_THEME_GLYPH = (226, 236, 245, 255)
ERASE = (0, 0, 0, 0)


def draw_railcar(color: tuple[int, int, int, int]) -> Image.Image:
    """Return a front-facing railcar glyph on a transparent field.

    A railcar front rather than converging rails: at the 24px Home Assistant
    actually renders these at, rails turn into an unreadable ladder, while a
    silhouette with a windshield and two lights still reads as a train.
    """
    scale = CANVAS / 512
    image = Image.new("RGBA", (CANVAS, CANVAS), ERASE)
    draw = ImageDraw.Draw(image)

    def box(*points: float) -> tuple[float, ...]:
        return tuple(point * scale for point in points)

    # Body: rounded at the roof, square at the floor. Drawn as a rounded
    # rectangle with the lower corners filled back in.
    draw.rounded_rectangle(box(96, 40, 416, 448), radius=104 * scale, fill=color)
    draw.rectangle(box(96, 300, 416, 448), fill=color)

    # Windshield, knocked out rather than painted so the glyph stays a single
    # colour and works on any background.
    draw.rounded_rectangle(box(148, 104, 364, 244), radius=44 * scale, fill=ERASE)

    # Headlights.
    draw.ellipse(box(150, 300, 206, 356), fill=ERASE)
    draw.ellipse(box(306, 300, 362, 356), fill=ERASE)

    # Skirt cutaways, so the base reads as a vehicle rather than a slab.
    draw.rectangle(box(96, 408, 168, 448), fill=ERASE)
    draw.rectangle(box(344, 408, 416, 448), fill=ERASE)

    # Rail beneath, trimmed to the glyph's width.
    draw.rounded_rectangle(box(72, 464, 440, 496), radius=16 * scale, fill=color)

    return image


def trim_to_square(image: Image.Image, margin: float = 0.02) -> Image.Image:
    """Crop to the glyph and re-centre it on a square field.

    The brands guidelines ask for images trimmed to minimize empty space, and
    drawing to fixed coordinates leaves more padding than intended. Cropping
    to the alpha bounding box makes the glyph fill the frame regardless of how
    the drawing code changes.
    """
    bbox = image.getbbox()
    if bbox is None:
        return image

    glyph = image.crop(bbox)
    side = int(max(glyph.size) * (1 + margin * 2))
    square = Image.new("RGBA", (side, side), ERASE)
    square.paste(
        glyph,
        ((side - glyph.width) // 2, (side - glyph.height) // 2),
    )
    return square


def write(image: Image.Image, name: str) -> None:
    """Downsample and write one icon at both supported sizes."""
    image = trim_to_square(image)
    for size in SIZES:
        suffix = "@2x" if size == 512 else ""
        path = BRAND_DIR / f"{name}{suffix}.png"
        resized = image.resize((size, size), Image.Resampling.LANCZOS)
        resized.save(path, "PNG", optimize=True)
        print(f"  {path.relative_to(BRAND_DIR.parent.parent.parent)} ({size}x{size})")


def main() -> None:
    """Generate every brand image."""
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    print("Writing brand images:")
    write(draw_railcar(LIGHT_THEME_GLYPH), "icon")
    write(draw_railcar(DARK_THEME_GLYPH), "dark_icon")


if __name__ == "__main__":
    main()
