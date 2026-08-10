"""Serve the Lovelace card that ships with this integration.

The card could have been a separate HACS repository, and that is what most
custom cards are. It is not one here because HACS installs one category per
repository: a card in its own repository is a second thing to install, a
second thing to update, and a version that can silently disagree with the
entity attributes it reads. Serving it from the integration means the card and
the sensors it renders can never be out of step.

The cost is that the bundle has to be committed rather than built on install
-- HACS copies files and runs nothing -- so `frontend/` holds the source and
CI rebuilds it to check the committed copy still matches.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

# Where the browser asks for the bundle, and where it lives on disk.
URL: Final = f"/{DOMAIN}/njtransit-card.js"
BUNDLE: Final = "njtransit-card.js"

# Set once the card has been registered, so a second commute does not register
# it again. Registering the same static path twice raises, and adding the same
# URL twice would load the module twice -- the second load re-runs
# `customElements.define`, which throws on an already-defined name and takes
# the rest of that module's registration with it.
_REGISTERED: Final = f"{DOMAIN}_card_registered"


@callback
def bundle_path() -> Path:
    """Return the on-disk path of the built card."""
    return Path(__file__).parent / "frontend" / BUNDLE


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and ask the frontend to load it.

    A missing bundle is not fatal. Someone running from a source checkout
    without a build has an integration that works and a card that does not,
    which is a better failure than no sensors at all.
    """
    if hass.data.get(_REGISTERED):
        return

    path = bundle_path()
    if not await hass.async_add_executor_job(path.is_file):
        return

    hass.data[_REGISTERED] = True

    await hass.http.async_register_static_paths(
        # No cache headers: the URL carries no content hash, so a cached copy
        # would outlive an integration update and leave a card rendering
        # attributes the sensors had stopped reporting.
        [StaticPathConfig(URL, str(path), False)]
    )
    add_extra_js_url(hass, URL)
