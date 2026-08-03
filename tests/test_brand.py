"""The brand images shipped with the integration.

Home Assistant serves these directly from the integration directory, so a
malformed file shows up as a broken icon in the UI rather than a failed
import. These assertions are the only thing standing between a bad
regeneration and that.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

BRAND_DIR = Path(__file__).parent.parent / "custom_components" / "njtransit" / "brand"

# Filename -> required square dimension, per the home-assistant/brands spec.
EXPECTED = {
    "icon.png": 256,
    "icon@2x.png": 512,
    "dark_icon.png": 256,
    "dark_icon@2x.png": 512,
}


@pytest.mark.parametrize(("name", "size"), EXPECTED.items())
def test_icon_matches_the_brands_spec(name: str, size: int) -> None:
    """Each icon is a square PNG at the documented dimension."""
    path = BRAND_DIR / name
    assert path.is_file(), f"{name} is missing"

    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == (size, size), f"{name} is {image.size}"


@pytest.mark.parametrize("name", EXPECTED)
def test_icon_has_transparency(name: str) -> None:
    """Transparency is preferred, and the glyph relies on knocked-out shapes.

    The windshield and headlights are erased rather than painted, so losing
    the alpha channel turns the icon into a featureless blob.
    """
    with Image.open(BRAND_DIR / name) as image:
        assert image.mode == "RGBA"
        alpha = image.getchannel("A")
        assert alpha.getextrema() == (0, 255), f"{name} has no transparent pixels"


@pytest.mark.parametrize("name", EXPECTED)
def test_icon_is_trimmed(name: str) -> None:
    """The glyph fills its frame, as the brands guidelines require."""
    with Image.open(BRAND_DIR / name) as image:
        bbox = image.getbbox()
        assert bbox is not None, f"{name} is blank"

        height = image.size[1]
        # Square canvas, taller-than-wide glyph: the vertical extent is what
        # the trim controls, so only that is asserted tightly.
        used = (bbox[3] - bbox[1]) / height
        assert used > 0.9, f"{name} wastes {(1 - used):.0%} of its height"


def test_light_and_dark_differ() -> None:
    """A dark variant that matches the light one is a generation bug."""
    with (
        Image.open(BRAND_DIR / "icon.png") as light,
        Image.open(BRAND_DIR / "dark_icon.png") as dark,
    ):
        assert light.tobytes() != dark.tobytes()


def test_no_logo_is_shipped() -> None:
    """Deliberate, and worth failing loudly if someone adds one.

    NJ Transit's wordmark is their trademark and this integration is
    unaffiliated. A logo.png is where the temptation to reproduce it lives;
    Home Assistant falls back to the icon without one. If a genuinely
    original logo is ever added, delete this test along with it.
    """
    assert not list(BRAND_DIR.glob("*logo*")), (
        "a logo was added -- confirm it is original artwork, not NJ Transit's mark"
    )
