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

import ipaddress
import json
import re
import socket
from urllib.parse import urljoin, urlparse

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


def _text(value) -> str:
    """Coerce a schema.org / model value to a plain string.

    schema.org fields (and sloppy model output) may be strings, lists, or
    nested objects — this flattens all of them so callers never do ``.strip()``
    on a list and 500.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return " ".join(_text(v) for v in value if v is not None).strip()
    if isinstance(value, dict):
        return _text(
            value.get("name") or value.get("text") or value.get("@value") or ""
        )
    return str(value).strip()


def _first_servings(recipe_yield) -> int:
    for v in _as_list(recipe_yield):
        m = re.search(r"\d+", str(v))
        if m:
            return int(m.group())
    return 0


def _instruction_text(step) -> str:
    if isinstance(step, dict) and step.get("@type") == "HowToSection":
        return ""  # sections are handled by flattening their itemListElement
    return _text(step)


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
        {"display": _text(i)}
        for i in _as_list(node.get("recipeIngredient"))
        if _text(i)
    ]
    steps = [{"text": t} for t in _flatten_instructions(node.get("recipeInstructions"))]
    prep = _iso_duration_to_minutes(node.get("prepTime"))
    cook = _iso_duration_to_minutes(node.get("cookTime"))
    total = _iso_duration_to_minutes(node.get("totalTime")) or (prep + cook)
    return {
        "name": _text(node.get("name")) or "Imported Recipe",
        "description": _text(node.get("description")),
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
        {"display": _text(i.get("display") if isinstance(i, dict) else i)}
        for i in _as_list(payload.get("ingredients"))
    ]
    steps = [
        {"text": _text(s.get("text") if isinstance(s, dict) else s)}
        for s in _as_list(payload.get("steps"))
    ]
    return {
        "name": _text(payload.get("name")) or "Imported Recipe",
        "description": _text(payload.get("description")),
        "recipeYield": _text(payload.get("recipeYield")),
        "servings": _int(payload.get("servings")),
        "prepMinutes": _int(payload.get("prepMinutes")),
        "cookMinutes": _int(payload.get("cookMinutes")),
        "totalMinutes": _int(payload.get("totalMinutes")),
        "ingredients": [i for i in ings if i["display"]],
        "steps": [s for s in steps if s["text"]],
        "notes": _text(payload.get("notes")),
    }


class UnsafeURLError(ValueError):
    """Raised when a URL targets a non-public / non-http destination."""


_MAX_FETCH_BYTES = 3_000_000
_MAX_REDIRECTS = 5


def _assert_public_url(url: str):
    """Reject non-http(s) schemes and hosts that resolve to private ranges.

    This is the SSRF guard: myMeal fetches user-supplied URLs server-side, and
    without this a group member could point it at localhost, the HA supervisor,
    a bundled Ollama, cloud metadata, or the LAN.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("only http(s) URLs can be imported")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("invalid URL host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve host: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise UnsafeURLError("refusing to fetch a private/internal address")


def _fetch(url: str) -> str:
    """Fetch a page, validating each redirect hop and capping the body size."""
    headers = {"User-Agent": "myMeal/0.1 (+recipe importer)"}
    current = url
    with httpx.Client(follow_redirects=False, timeout=20, headers=headers) as client:
        for _ in range(_MAX_REDIRECTS):
            _assert_public_url(current)
            with client.stream("GET", current) as r:
                if r.is_redirect and r.headers.get("location"):
                    current = urljoin(current, r.headers["location"])
                    continue
                r.raise_for_status()
                total = 0
                chunks: list[bytes] = []
                for chunk in r.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= _MAX_FETCH_BYTES:
                        break
                body = b"".join(chunks)
                return body.decode(r.encoding or "utf-8", errors="replace")
    raise UnsafeURLError("too many redirects")


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
