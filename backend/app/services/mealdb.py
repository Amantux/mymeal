"""TheMealDB (themealdb.com) — a free, open recipe database, used for import
by name.

This is the source that makes "By name" work with NOTHING configured. Before it,
importing by name required an Ollama web-search key, so a fresh install's
"By name" tab was a dead end; now the lookup order is:

    TheMealDB (free, structured, instant) -> web search (key-gated fallback)

Deterministic first, model last — the same rule as the rest of the importer.
TheMealDB returns fully structured recipes (ingredient/measure pairs, split
instructions, an image), so a hit never touches the AI provider at all.

Terms note, stated rather than buried: the public test key ("1") is offered by
TheMealDB for development and educational use, which a self-hosted household app
fits, but MYMEAL_MEALDB_KEY lets an operator use their own supporter key. Every
imported recipe keeps its TheMealDB source URL.

Best-effort and bounded like websearch.py: never raises to the caller — an
empty result simply lets the import fall through to the next source.
"""
from __future__ import annotations

import logging
import re

import httpx

_LOGGER = logging.getLogger("mymeal.mealdb")
_BASE = "https://www.themealdb.com/api/json/v1"
_TIMEOUT = 12.0
_MAX_INGREDIENTS = 40


def _key() -> str:
    from .ai.settings_access import resolved
    return str(getattr(resolved(None), "MEALDB_KEY", "") or "").strip() or "1"


def _get(path: str, params: dict) -> dict | None:
    try:
        r = httpx.get(f"{_BASE}/{_key()}/{path}", params=params, timeout=_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        _LOGGER.warning("TheMealDB lookup failed: %s", type(exc).__name__)
        return None


def _split_instructions(text: str) -> list[dict]:
    """TheMealDB instructions arrive as one newline-y blob, frequently with
    "STEP 1"-style prefixes. One step per meaningful line."""
    steps = []
    for ln in (text or "").replace("\r\n", "\n").split("\n"):
        ln = re.sub(r"^\s*(?:step\s*\d+\s*[:.)-]?|\d+\s*[.)])\s*", "", ln.strip(),
                    flags=re.I).strip()
        if ln:
            steps.append({"title": "", "text": ln[:2000]})
    return steps[:60]


def _to_payload(meal: dict) -> dict:
    """One TheMealDB `meals[]` object -> the importer's payload shape.

    Ingredients live in twenty numbered column pairs (strIngredient1/strMeasure1
    ...), most of them blank — the API's shape, not ours. Joined back into the
    single display line the rest of the app expects ("1 cup Flour").
    """
    ingredients = []
    for i in range(1, _MAX_INGREDIENTS + 1):
        food = str(meal.get(f"strIngredient{i}") or "").strip()
        if not food:
            continue
        measure = str(meal.get(f"strMeasure{i}") or "").strip()
        display = f"{measure} {food}".strip()
        ingredients.append({"display": display})

    tags = [t.strip() for t in str(meal.get("strTags") or "").split(",") if t.strip()]
    for extra in (meal.get("strCategory"), meal.get("strArea")):
        v = str(extra or "").strip()
        if v and v.lower() not in {t.lower() for t in tags}:
            tags.append(v)

    return {
        "name": str(meal.get("strMeal") or "").strip() or "Imported Recipe",
        "description": "",
        "recipeYield": "",
        "servings": 0,           # TheMealDB doesn't state one; never invent it
        "prepMinutes": 0,
        "cookMinutes": 0,
        "totalMinutes": 0,
        "cookTemperatureC": None,
        "ingredients": ingredients,
        "steps": _split_instructions(str(meal.get("strInstructions") or "")),
        "tags": tags[:12],
        "imageUrl": str(meal.get("strMealThumb") or "").strip(),
        "notes": "",
        "sourceUrl": str(meal.get("strSource") or "").strip()
        or f"https://www.themealdb.com/meal/{meal.get('idMeal', '')}",
    }


def search(query: str) -> dict | None:
    """The best TheMealDB match for ``query`` as an import payload, or None.

    Exact-ish name search first; when that misses, TheMealDB's search is
    forgiving enough that a first-word retry catches "beef wellington recipe"
    style queries. None means "not found here" — the caller falls through to the
    next source, so this must never raise.
    """
    q = (query or "").strip()
    if not q:
        return None
    data = _get("search.php", {"s": q[:100]})
    meals = (data or {}).get("meals") or []
    if not meals:
        # Drop noise words a person types but a database title doesn't carry.
        slim = re.sub(r"\b(?:recipe|recipes|easy|best|homemade)\b", "", q,
                      flags=re.I).strip()
        if slim and slim.lower() != q.lower():
            data = _get("search.php", {"s": slim[:100]})
            meals = (data or {}).get("meals") or []
    if not meals:
        return None
    payload = _to_payload(meals[0])
    # A recipe with no ingredients is a broken row, not a result.
    return payload if payload["ingredients"] else None
