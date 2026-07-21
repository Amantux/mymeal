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
        """Build from the effective config: this group's DB overrides (set in
        the UI) merged onto the env / add-on defaults, so a connection set in
        Home Assistant OR the UI is honored."""
        from .ai.settings_access import resolved
        from .edibl_config import effective

        cfg = resolved(settings)
        gid = None
        try:
            from flask import g, has_request_context
            gid = g.current_group.id if (has_request_context()
                                         and getattr(g, "current_group", None)) else None
        except Exception:  # noqa: BLE001
            gid = None
        eff = effective(cfg, gid)
        return cls(eff["url"], eff["token"], timeout=float(cfg.HTTP_TIMEOUT_SECONDS))

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _headers(self) -> dict:
        # Edibl authenticates callers with Authorization: Bearer <token>
        # (its tokens API), the same scheme myMeal accepts. No token is valid
        # only if Edibl itself runs with auth disabled behind ingress.
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _finish(self, call) -> dict:
        """Run an httpx call and classify the outcome as {ok, reachable, ...}.

        `ok` is True ONLY on a 2xx with a body. `reachable` distinguishes
        "server answered with an error" (True — e.g. 401/500) from "could not
        reach the server" (False). Callers that need USABLE data must check
        `ok`, not `reachable`: a 401 is reachable but useless, and treating it
        as success is how a bad token silently became "empty inventory".
        """
        if not self.configured:
            return {"ok": False, "configured": False, "reachable": False}
        try:
            r = call()
            r.raise_for_status()
            return {"ok": True, "configured": True, "reachable": True, "data": r.json()}
        except httpx.HTTPStatusError as exc:
            logger.warning("edibl request -> HTTP %s", exc.response.status_code)
            return {"ok": False, "configured": True, "reachable": True,
                    "error": f"HTTP {exc.response.status_code}", "status": exc.response.status_code}
        except httpx.HTTPError as exc:
            logger.warning("edibl request failed: %s", exc)
            return {"ok": False, "configured": True, "reachable": False, "error": str(exc)}

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._finish(lambda: httpx.get(
            f"{self.base_url}{path}", params=params,
            headers=self._headers(), timeout=self.timeout))

    def _post(self, path: str, payload: dict) -> dict:
        return self._finish(lambda: httpx.post(
            f"{self.base_url}{path}", json=payload,
            headers=self._headers(), timeout=self.timeout))

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
                "ok": probe.get("ok", False), "url": self.base_url,
                # Surface the error (e.g. HTTP 401) when the server answered but
                # not with usable data, so a bad token is diagnosable.
                "detail": probe.get("data") if probe.get("ok") else probe.get("error")}

    def push_plan(self, items: list[dict], meal: str = "", source: str = "mymeal") -> dict:
        """Send planned ingredients to Edibl. Matches Edibl's documented body:
        {meal?, source?, items:[{name, quantity?, unit?, neededBy?, sourceRef?}]}."""
        return self._post("/api/v1/integrations/mymeal/plan",
                          {"meal": meal, "source": source, "items": items})

    def on_hand(self) -> dict:
        """Inventory for recipe matching. Returns
        {available: bool, reason?: str, items: [{name,...}]}.

        `available` is False (with a reason) when Edibl is not configured or
        unreachable, so callers can tell the user to connect Edibl instead of
        silently ranking against an empty inventory.
        """
        if not self.configured:
            return {"available": False, "items": [],
                    "reason": "Edibl is not configured. Inventory-aware features "
                              "need a companion Edibl instance (set MYMEAL_EDIBL_URL)."}
        stock = self.get_stock()
        # Gate on `ok`, not `reachable`: a 401 (bad token) or 500 is reachable
        # but did NOT give us inventory, so the feature is unavailable — never
        # rank against a falsely-empty stock and claim Edibl is fine.
        if not stock.get("ok"):
            if stock.get("reachable"):
                reason = (f"Edibl responded with an error ({stock.get('error')}). "
                          "Check MYMEAL_EDIBL_API_TOKEN and that Edibl is healthy.")
            else:
                reason = f"Edibl is configured but unreachable: {stock.get('error')}"
            return {"available": False, "items": [], "reason": reason}
        return {"available": True, "items": stock["items"]}

    def get_stock(self) -> dict:
        """Read Edibl's current stock, normalised to myMeal's pantry shape:
        {name, quantity, unit}. Returns {configured, reachable, items}."""
        res = self._get("/api/v1/stock")
        if not res.get("ok"):
            # Reachable-but-errored (401/500) and unreachable both mean "no
            # usable stock". Carry ok/reachable/error through so callers can
            # tell the user which it was.
            return {"ok": False, "configured": self.configured,
                    "reachable": res.get("reachable", False),
                    "error": res.get("error"), "items": []}
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
        return {"ok": True, "configured": True, "reachable": True, "items": items}
