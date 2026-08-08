from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout
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

    # --- Recipes: CRUD + versions/experiments ---------------------------
    async def _put(self, path: str, json: dict | None = None):
        url = build_url(self.host, self.port, path)
        async with self._session.put(
            url, json=json or {}, headers=self._headers, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _delete(self, path: str) -> bool:
        url = build_url(self.host, self.port, path)
        async with self._session.delete(
            url, headers=self._headers, timeout=_TIMEOUT
        ) as resp:
            resp.raise_for_status()
            return True

    async def resolve_recipe(self, name_or_id: str) -> dict:
        """Find one recipe from an id, slug, or name.

        Delegates to myMeal's ``/recipes/resolve``, which ranks matches by name,
        tags and description and answers with a confidence. That endpoint is the
        single implementation of the policy — the MCP server uses it too, so a
        name resolves the same way by voice, by service call, and in the app.

        Returns ``{"status": "ok", "recipe": {...}}`` when confident, or
        ``needs_clarification`` (with ranked ``candidates`` carrying tags and a
        description) / ``not_found`` / ``error``. Callers must never guess on
        ``needs_clarification`` — acting on a partial name match is how the wrong recipe
        gets edited or deleted.
        """
        ident = (name_or_id or "").strip()
        if not ident:
            return {"status": "error", "error": "No recipe name or id given."}
        try:
            data = await self._get("/api/v1/recipes/resolve", {"q": ident})
        except ClientResponseError as err:
            return {"status": "error", "error": f"myMeal returned {err.status}."}
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error", "error": "Could not reach myMeal."}

        confidence = data.get("confidence")
        if confidence == "high":
            return {"status": "ok", "recipe": data.get("recipe") or {}}
        if confidence == "low":
            candidates = data.get("candidates") or []
            return {
                "status": "needs_clarification",
                "error": (
                    f"{len(candidates)} recipes match '{ident}'. "
                    "Pass the id of the one you mean."
                ),
                "candidates": candidates,
            }
        return {"status": "not_found", "error": f"No recipe matching '{ident}'."}

    async def search_recipes(self, query: str) -> dict:
        # /search, not /recipes?q=: the MCP server already uses /search, which
        # RANKS by relevance and also matches tags. Against /recipes?q= a recipe
        # matching only by tag ("curry") was findable through Assist/MCP and
        # invisible through this service, and identical queries came back in a
        # different order on each surface.
        try:
            data = await self._get(
                "/api/v1/search", {"q": query, "types": "recipe"})
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error", "matches": []}
        return {
            "status": "ok",
            "matches": [
                {"id": r.get("id"), "name": r.get("name")}
                for r in data.get("results", [])
            ],
        }

    async def get_recipe(self, name_or_id: str) -> dict:
        found = await self.resolve_recipe(name_or_id)
        if found["status"] != "ok":
            return found
        r = found["recipe"]
        return {
            "status": "ok",
            "id": r.get("id"),
            "name": r.get("name"),
            "servings": r.get("servings"),
            "totalMinutes": r.get("totalMinutes"),
            "ingredients": [i.get("display") for i in r.get("ingredients", [])],
            "steps": [s.get("text") for s in r.get("steps", [])],
        }

    @staticmethod
    def _recipe_body(fields: dict) -> dict:
        """Translate service fields to the API's shape.

        Only keys the caller actually supplied are included: PUT /recipes/<id>
        REPLACES ingredients/steps whenever those keys are present, so passing
        them unconditionally would wipe the stored lists on a partial edit.
        """
        body: dict = {}
        if "name" in fields:
            body["name"] = fields["name"]
        if "servings" in fields:
            body["servings"] = fields["servings"]
        if "ingredients" in fields:
            body["ingredients"] = [{"display": str(x)} for x in fields["ingredients"]]
        if "steps" in fields:
            body["steps"] = [{"text": str(x)} for x in fields["steps"]]
        return body

    async def add_recipe(self, fields: dict) -> dict:
        try:
            r = await self._post("/api/v1/recipes", self._recipe_body(fields))
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error", "error": "Could not reach myMeal."}
        return {"status": "ok", "id": r.get("id"), "name": r.get("name")}

    async def update_recipe(self, name_or_id: str, fields: dict) -> dict:
        found = await self.resolve_recipe(name_or_id)
        if found["status"] != "ok":
            return found
        body = self._recipe_body(fields)
        if not body:
            return {"status": "error", "error": "Nothing to update."}
        try:
            r = await self._put(f"/api/v1/recipes/{found['recipe']['id']}", body)
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error", "error": "Could not reach myMeal."}
        return {"status": "ok", "id": r.get("id"), "name": r.get("name"),
                "updated": sorted(body)}

    async def delete_recipe(self, name_or_id: str, confirm: bool) -> dict:
        """Delete a recipe. Irreversible — it also removes its saved versions."""
        found = await self.resolve_recipe(name_or_id)
        if found["status"] != "ok":
            return found
        recipe = found["recipe"]
        if not confirm:
            versions = await self.list_recipe_versions(recipe["id"])
            n = len(versions.get("versions", [])) if versions["status"] == "ok" else 0
            return {
                "status": "confirm_required",
                "id": recipe.get("id"),
                "name": recipe.get("name"),
                "versions": n,
                "error": (
                    f"This would permanently delete '{recipe.get('name')}'"
                    + (f" and its {n} saved version(s)/experiment(s)" if n else "")
                    + ". Call again with confirm: true to proceed."
                ),
            }
        try:
            await self._delete(f"/api/v1/recipes/{recipe['id']}")
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error", "error": "Could not reach myMeal."}
        return {"status": "ok", "deleted": recipe.get("name")}

    async def list_recipe_versions(self, name_or_id: str) -> dict:
        found = await self.resolve_recipe(name_or_id)
        if found["status"] != "ok":
            return found
        try:
            data = await self._get(f"/api/v1/recipes/{found['recipe']['id']}/versions")
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error", "error": "Could not reach myMeal."}
        return {
            "status": "ok",
            "recipe": found["recipe"].get("name"),
            "versions": [
                {
                    "id": v.get("id"),
                    "kind": v.get("kind"),
                    "status": v.get("status"),
                    "label": v.get("label"),
                    "rating": v.get("rating"),
                }
                for v in data.get("items", [])
            ],
        }

    async def start_recipe_experiment(
        self, name_or_id: str, label: str, note: str = ""
    ) -> dict:
        found = await self.resolve_recipe(name_or_id)
        if found["status"] != "ok":
            return found
        try:
            v = await self._post(
                f"/api/v1/recipes/{found['recipe']['id']}/versions",
                {"label": label, "note": note},
            )
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error", "error": "Could not reach myMeal."}
        return {"status": "ok", "versionId": v.get("id"), "label": v.get("label"),
                "recipe": found["recipe"].get("name")}

    async def _version_action(
        self, name_or_id: str, version_id: str, action: str, body: dict | None = None
    ) -> dict:
        found = await self.resolve_recipe(name_or_id)
        if found["status"] != "ok":
            return found
        path = f"/api/v1/recipes/{found['recipe']['id']}/versions/{version_id}/{action}"
        try:
            data = await self._post(path, body or {})
        except (ClientError, asyncio.TimeoutError):
            return {"status": "error", "error": "Could not reach myMeal."}
        return {"status": "ok", "recipe": data.get("name") or found["recipe"].get("name")}

    async def add_experiment_feedback(
        self, name_or_id: str, version_id: str, fields: dict
    ) -> dict:
        body = {k: fields[k] for k in ("rating", "feedback") if k in fields}
        return await self._version_action(name_or_id, version_id, "feedback", body)

    async def promote_experiment(self, name_or_id: str, version_id: str) -> dict:
        return await self._version_action(name_or_id, version_id, "promote")

    async def restore_recipe_version(self, name_or_id: str, version_id: str) -> dict:
        return await self._version_action(name_or_id, version_id, "restore")
