"""Client for a companion Edibl instance (github.com/Amantux/edibl).

Edibl is myMeal's sibling: it tracks the real food inventory (what is on hand,
how fresh). myMeal plans meals and shopping. The two fit together directly —
Edibl already ships myMeal-named endpoints
(``POST /integrations/mymeal/plan``, ``POST /integrations/mymeal/pull``), and
this is myMeal's half of that contract.

Two directions:
  * PUSH — myMeal sends planned ingredients to Edibl's
    ``/integrations/mymeal/plan`` so Edibl can reconcile them against stock.
  * PULL — myMeal reads Edibl's ``/stock`` so pantry-aware features reflect
    real, fresh inventory.

Everything here is bounded and never raises into a request: an unreachable or
unconfigured Edibl degrades to "integration unavailable", exactly like an
absent AI provider. It never takes myMeal down.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class EdiblClient:
    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings=None) -> "EdiblClient":
        from .ai.settings_access import resolved

        cfg = resolved(settings)
        return cls(cfg.EDIBL_URL, cfg.EDIBL_API_TOKEN,
                   timeout=float(cfg.HTTP_TIMEOUT_SECONDS))

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict:
        # Edibl authenticates callers with Authorization: Bearer <token>
        # (its tokens API), the same scheme myMeal accepts. No token is valid
        # only if Edibl itself runs with auth disabled behind ingress.
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.configured:
            return {"configured": False, "reachable": False}
        try:
            r = httpx.get(f"{self.base_url}{path}", params=params,
                          headers=self._headers(), timeout=self.timeout)
            r.raise_for_status()
            return {"configured": True, "reachable": True, "data": r.json()}
        except httpx.HTTPStatusError as exc:
            return {"configured": True, "reachable": True,
                    "error": f"HTTP {exc.response.status_code}", "status": exc.response.status_code}
        except httpx.HTTPError as exc:
            logger.warning("edibl GET %s failed: %s", path, exc)
            return {"configured": True, "reachable": False, "error": str(exc)}

    def _post(self, path: str, payload: dict) -> dict:
        if not self.configured:
            return {"configured": False, "reachable": False}
        try:
            r = httpx.post(f"{self.base_url}{path}", json=payload,
                           headers=self._headers(), timeout=self.timeout)
            r.raise_for_status()
            return {"configured": True, "reachable": True, "data": r.json()}
        except httpx.HTTPStatusError as exc:
            return {"configured": True, "reachable": True,
                    "error": f"HTTP {exc.response.status_code}", "status": exc.response.status_code}
        except httpx.HTTPError as exc:
            logger.warning("edibl POST %s failed: %s", path, exc)
            return {"configured": True, "reachable": False, "error": str(exc)}

    # -- operations --------------------------------------------------------

    def status(self) -> dict:
        """Configured + reachable, for the Settings UI and a health signal.

        Reachability is a cheap GET; a down Edibl reports reachable=False rather
        than raising.
        """
        if not self.configured:
            return {"configured": False, "reachable": False}
        probe = self._get("/api/v1/integrations/status")
        return {"configured": True, "reachable": probe.get("reachable", False),
                "url": self.base_url,
                "detail": probe.get("data") if probe.get("reachable") else probe.get("error")}

    def push_plan(self, items: list[dict], meal: str = "", source: str = "mymeal") -> dict:
        """Send planned ingredients to Edibl. Matches Edibl's documented body:
        {meal?, source?, items:[{name, quantity?, unit?, neededBy?, sourceRef?}]}."""
        return self._post("/api/v1/integrations/mymeal/plan",
                          {"meal": meal, "source": source, "items": items})

    def get_stock(self) -> dict:
        """Read Edibl's current stock, normalised to myMeal's pantry shape:
        {name, quantity, unit}. Returns {configured, reachable, items}."""
        res = self._get("/api/v1/stock")
        if not res.get("reachable"):
            return {**res, "items": []}
        raw = (res.get("data") or {}).get("items", [])
        items = []
        for row in raw:
            name = (row.get("name") or (row.get("product") or {}).get("name") or "").strip()
            if not name:
                continue
            items.append({
                "name": name,
                "quantity": row.get("quantity"),
                "unit": row.get("unit"),
                "expiresAt": row.get("expiryDate") or row.get("expiresAt"),
            })
        return {"configured": True, "reachable": True, "items": items}
