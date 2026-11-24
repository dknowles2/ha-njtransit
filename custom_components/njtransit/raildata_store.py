"""Persistence for the RailData client, backed by Home Assistant storage.

The client itself must not import Home Assistant (SPEC 4.1), so it declares
what it needs as :class:`~.api.raildata.RailDataStore` and this supplies it.
One file per account: two commutes on the same credentials share a token and
share every station-day either of them fetched, which is what keeps the
daily limits from being spent twice.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import RAILDATA_STORAGE_KEY, RAILDATA_STORAGE_VERSION


class RailDataStorage:
    """A :class:`~.api.raildata.RailDataStore` over ``.storage``."""

    def __init__(self, hass: HomeAssistant, username: str) -> None:
        """Initialize storage for one account."""
        self._store: Store[dict[str, Any]] = Store(
            hass,
            RAILDATA_STORAGE_VERSION,
            f"{RAILDATA_STORAGE_KEY}.{_slug(username)}",
            private=True,
        )

    async def load(self) -> dict[str, Any] | None:
        """Return what was last saved, or ``None`` the first time."""
        return await self._store.async_load()

    async def save(self, data: dict[str, Any]) -> None:
        """Persist ``data``, replacing what was saved before."""
        await self._store.async_save(data)


def _slug(username: str) -> str:
    """Return a filename-safe form of an account name."""
    return "".join(
        character if character.isalnum() else "_" for character in username.casefold()
    )
