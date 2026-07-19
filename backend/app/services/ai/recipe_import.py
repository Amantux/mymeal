"""Recipe import: URL/text → a normalized recipe payload.

Strategy (cheapest first):

1. If given a URL, fetch it and look for embedded schema.org/JSON-LD ``Recipe``
   markup — the structured data most recipe sites already publish. This is
   deterministic and costs no tokens.
2. Otherwise (no markup, or raw pasted text), hand the visible text to the
   configured AI provider and ask for the same normalized shape.

Both paths return the camelCase payload the recipes API's ``_apply`` accepts.
"""
from __future__ import annotations

import json
import re

import httpx
from bs4 import BeautifulSoup

from .base import AIProvider

_IMPORT_SYSTEM = (
    "You extract a single cooking recipe from the text a user provides and "
    "return it as structured data. Convert all times to whole minutes. If a "
    "field is unknown, use an empty string, 0, or an empty list. Never invent "
    "ingredients or steps that are not present in the source."
)

_SCHEMA_HINT = """Return JSON with exactly these keys:
{
  "name": string,
  "description": string,
  "recipeYield": string,          // e.g. "4 servings", "1 loaf"
  "servings": integer,            // numeric serving count, 0 if unknown
  "prepMinutes": integer,
  "cookMinutes": integer,
  "totalMinutes": integer,
  "ingredients": [ { "display": string } ],   // one entry per ingredient line
  "steps": [ { "text": string } ],            // one entry per instruction step
  "notes": string
}"""


def _iso_duration_to_minutes(value) -> int:
    """Parse an ISO-8601 duration (``PT1H30M``) to whole minutes; 0 on failure."""
    if not value or not isinstance(value, str):
        return 0
    m = re.match(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?", value.strip())
    if not m:
        return 0
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    return hours * 60 + minutes


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _first_servings(recipe_yield) -> int:
    for v in _as_list(recipe_yield):
        m = re.search(r"\d+", str(v))
        if m:
            return int(m.group())
    return 0


def _instruction_text(step) -> str:
    if isinstance(step, str):
        return step.strip()
    if isinstance(step, dict):
        # HowToStep / HowToSection
        if step.get("@type") == "HowToSection":
            return ""  # sections are handled by flattening their itemListElement
        return (step.get("text") or step.get("name") or "").strip()
    return ""


def _flatten_instructions(instructions) -> list[str]:
    out: list[str] = []
    for item in _as_list(instructions):
        if isinstance(item, dict) and item.get("@type") == "HowToSection":
            out += _flatten_instructions(item.get("itemListElement"))
        else:
            text = _instruction_text(item)
            if text:
                out.append(text)
    # A single blob of text with newlines → split into steps.
    if len(out) == 1 and "\n" in out[0]:
        out = [s.strip() for s in out[0].split("\n") if s.strip()]
    return out


def normalize_jsonld(node: dict) -> dict:
    """Map a schema.org Recipe node to our payload shape."""
    yield_val = node.get("recipeYield")
    ingredients = [
        {"display": str(i).strip()}
        for i in _as_list(node.get("recipeIngredient"))
        if str(i).strip()
    ]
    steps = [{"text": t} for t in _flatten_instructions(node.get("recipeInstructions"))]
    prep = _iso_duration_to_minutes(node.get("prepTime"))
    cook = _iso_duration_to_minutes(node.get("cookTime"))
    total = _iso_duration_to_minutes(node.get("totalTime")) or (prep + cook)
    return {
        "name": (node.get("name") or "Imported Recipe").strip(),
        "description": (node.get("description") or "").strip(),
        "recipeYield": " ".join(str(v) for v in _as_list(yield_val))[:120],
        "servings": _first_servings(yield_val),
        "prepMinutes": prep,
        "cookMinutes": cook,
        "totalMinutes": total,
        "ingredients": ingredients,
        "steps": steps,
        "notes": "",
    }


def _find_recipe_node(data) -> dict | None:
    """Walk parsed JSON-LD looking for an object whose @type includes Recipe."""
    if isinstance(data, list):
        for item in data:
            found = _find_recipe_node(item)
            if found:
                return found
        return None
    if isinstance(data, dict):
        types = _as_list(data.get("@type"))
        if any(str(t).lower() == "recipe" for t in types):
            return data
        if "@graph" in data:
            return _find_recipe_node(data["@graph"])
    return None


def extract_jsonld_recipe(html: str) -> dict | None:
    """Return the first schema.org Recipe found in a page's JSON-LD, or None."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        node = _find_recipe_node(data)
        if node:
            return node
    return None


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for junk in soup(["script", "style", "nav", "footer", "header"]):
        junk.decompose()
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)[:12000]  # cap to keep token cost bounded


def _normalize_ai(payload: dict) -> dict:
    """Coerce an AI-returned object to the exact payload shape/types."""
    def _int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    ings = [
        {"display": str(i.get("display", i) if isinstance(i, dict) else i).strip()}
        for i in (payload.get("ingredients") or [])
    ]
    steps = [
        {"text": str(s.get("text", s) if isinstance(s, dict) else s).strip()}
        for s in (payload.get("steps") or [])
    ]
    return {
        "name": (payload.get("name") or "Imported Recipe").strip(),
        "description": (payload.get("description") or "").strip(),
        "recipeYield": (payload.get("recipeYield") or "").strip(),
        "servings": _int(payload.get("servings")),
        "prepMinutes": _int(payload.get("prepMinutes")),
        "cookMinutes": _int(payload.get("cookMinutes")),
        "totalMinutes": _int(payload.get("totalMinutes")),
        "ingredients": [i for i in ings if i["display"]],
        "steps": [s for s in steps if s["text"]],
        "notes": (payload.get("notes") or "").strip(),
    }


def _fetch(url: str) -> str:
    headers = {"User-Agent": "myMeal/0.1 (+recipe importer)"}
    r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return r.text


def import_recipe(
    *, url: str = "", text: str = "", provider: AIProvider | None = None
) -> dict:
    """Return a normalized recipe payload from a URL or raw text.

    ``provider`` is required for the AI fallback (and for text-only input). URL
    input first tries deterministic JSON-LD extraction and only falls back to
    the provider when no structured markup is found.
    """
    source_url = url.strip()
    html = ""
    if source_url:
        html = _fetch(source_url)
        node = extract_jsonld_recipe(html)
        if node:
            payload = normalize_jsonld(node)
            payload["sourceUrl"] = source_url
            return payload

    # AI path: raw text, or a page with no usable markup.
    if provider is None:
        raise ValueError("no AI provider available for recipe parsing")
    body = text.strip() or _visible_text(html)
    prompt = f"{_SCHEMA_HINT}\n\nSource text:\n\n{body}"
    payload = _normalize_ai(provider.complete_json(prompt, system=_IMPORT_SYSTEM))
    if source_url:
        payload["sourceUrl"] = source_url
    return payload
