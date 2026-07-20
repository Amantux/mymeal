from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_SUMMARY_PATH,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .helpers import build_url

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = ClientTimeout(total=15)


class MyMealDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the myMeal ``/ha/summary`` endpoint (counts + this week's meals)."""

    def __init__(
        self, hass: HomeAssistant, session: ClientSession, entry: ConfigEntry
    ) -> None:
        self._session = session
        self.host = entry.data[CONF_HOST]
        self.port = int(entry.data[CONF_PORT])
        # Long-lived API token for auth-enabled (standalone) servers. Empty for
        # the add-on, which runs auth-disabled behind ingress.
        token = entry.data.get(CONF_TOKEN, "")
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._summary_url = build_url(self.host, self.port, DEFAULT_SUMMARY_PATH)
        interval = int(entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL))
        super().__init__(
            hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=interval)
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            async with self._session.get(
                self._summary_url, headers=self._headers, timeout=_TIMEOUT
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except (ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Error fetching myMeal summary: {err}") from err

    # --- Helpers used by voice intents + services -----------------------
    async def _get(self, path: str, params: dict | None = None):
        url = build_url(self.host, self.port, path)
        async with self._session.get(
            url, params=params or {}, headers=self._headers, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _post(self, path: str, json: dict | None = None):
        url = build_url(self.host, self.port, path)
        async with self._session.post(
            url, json=json or {}, headers=self._headers, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def whats_for_dinner(self, day: str = "") -> dict:
        when = day or date.today().isoformat()
        try:
            data = await self._get("/api/v1/mealplans", {"start": when, "end": when})
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error"}
        meals = [
            {
                "mealType": e["mealType"],
                "name": (e.get("recipe") or {}).get("name") or e.get("title"),
            }
            for e in data.get("items", [])
        ]
        return {"status": "ok", "date": when, "meals": meals}

    async def what_can_i_cook(self) -> dict:
        try:
            data = await self._post("/api/v1/ai/suggest", {"limit": 5})
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error"}
        return {"status": "ok", "suggestions": data.get("suggestions", []),
                "ediblAvailable": data.get("ediblAvailable", True),
                "message": data.get("message")}

    async def add_to_shopping_list(self, item: str) -> dict:
        try:
            lists = (await self._get("/api/v1/shopping-lists")).get("items", [])
            sl = lists[0] if lists else await self._post(
                "/api/v1/shopping-lists", {"name": "Shopping List"}
            )
            await self._post(
                f"/api/v1/shopping-lists/{sl['id']}/items", {"display": item}
            )
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error", "item": item}
        return {"status": "ok", "item": item, "list": sl["name"]}

    async def plan_week(self, days: int = 7, preferences: str = "") -> dict:
        try:
            data = await self._post(
                "/api/v1/ai/plan", {"days": days, "preferences": preferences}
            )
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error"}
        return {"status": "ok", "planned": len(data.get("entries", []))}
