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
  "tags": [ string ],             // short labels: cuisine, course, diet, method
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


def _instruction_step(step) -> dict:
    """One instruction as ``{"title": ..., "text": ...}``.

    A ``HowToStep`` carries the instruction in ``text`` and an optional heading
    in ``name``. Generic ``_text()`` prefers ``name`` — correct for an
    ImageObject or a Person, catastrophic here: it returned "Mix the dough" and
    threw the actual instruction away, so a well-marked-up recipe imported as a
    list of headings with no method. (It also blinded the temperature scan
    below, which reads the step text.)

    RecipeStep has a ``title`` column, so both are kept rather than one being
    picked over the other.
    """
    if isinstance(step, dict) and step.get("@type") == "HowToSection":
        return {}  # sections are handled by flattening their itemListElement
    if isinstance(step, dict):
        text = _text(step.get("text") or step.get("@value") or "")
        title = _text(step.get("name") or "")
        if text:
            # A heading identical to the body is noise, not a title.
            return {"title": "" if title == text else title, "text": text}
        # No `text` at all — fall back to whatever _text can find (often `name`),
        # so a sloppily-marked-up step is still imported rather than dropped.
        return {"title": "", "text": _text(step)}
    return {"title": "", "text": _text(step)}


def _flatten_instructions(instructions) -> list[dict]:
    out: list[dict] = []
    for item in _as_list(instructions):
        if isinstance(item, dict) and item.get("@type") == "HowToSection":
            out += _flatten_instructions(item.get("itemListElement"))
        else:
            step = _instruction_step(item)
            if step.get("text"):
                out.append(step)
    # A single blob of text with newlines → split into steps.
    if len(out) == 1 and "\n" in out[0]["text"]:
        out = [{"title": "", "text": s.strip()}
               for s in out[0]["text"].split("\n") if s.strip()]
    return out


def _abs_url(base: str, u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    return urljoin(base, u) if base else u


def _first_image(value, base: str = "") -> str:
    """schema.org ``image`` may be a URL string, a list, or an ImageObject
    ({url|contentUrl|@id}). Return the first usable absolute URL."""
    for v in _as_list(value):
        if isinstance(v, str) and v.strip():
            return _abs_url(base, v)
        if isinstance(v, dict):
            u = v.get("url") or v.get("contentUrl") or v.get("@id")
            if u:
                return _abs_url(base, str(u))
    return ""


def _extract_tags(node: dict) -> list[str]:
    """Derive tag names from keywords + cuisine + course. Keywords may be a
    comma-joined string or a list; dedupe case-insensitively, cap the count."""
    raw: list[str] = []
    kw = node.get("keywords")
    if isinstance(kw, str):
        raw += [p for p in kw.split(",")]
    else:
        raw += [_text(k) for k in _as_list(kw)]
    raw += [_text(c) for c in _as_list(node.get("recipeCuisine"))]
    raw += [_text(c) for c in _as_list(node.get("recipeCategory"))]
    out, seen = [], set()
    for t in raw:
        t = t.strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:12]


def normalize_jsonld(node: dict, base_url: str = "") -> dict:
    """Map a schema.org Recipe node to our payload shape."""
    yield_val = node.get("recipeYield")
    ingredients = [
        {"display": _text(i)}
        for i in _as_list(node.get("recipeIngredient"))
        if _text(i)
    ]
    steps = _flatten_instructions(node.get("recipeInstructions"))
    # schema.org has no temperature field, so read it out of the instructions
    # (and cookingMethod, where some sites put "Bake at 180C").
    from ...services.cooking import parse_temperature
    temperature = parse_temperature(
        " ".join([_text(node.get("cookingMethod"))] + [s["text"] for s in steps]))

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
        "cookTemperatureC": temperature,
        "ingredients": ingredients,
        "steps": steps,
        "tags": _extract_tags(node),
        "imageUrl": _first_image(node.get("image"), base_url),
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


def extract_pasted_recipe(text: str):
    """A schema.org Recipe pasted directly, or None.

    Accepts what people actually paste: a bare JSON object, a ``@graph``, an
    array of nodes, a copied ``<script type="application/ld+json">`` block, or a
    whole page's HTML.

    This exists so pasting structured markup does NOT go to the model. It is
    already the exact shape the importer wants, so sending it to an LLM spends a
    call to re-derive what is in front of us, can get it wrong, and — worse —
    fails outright with "no AI provider configured" for anyone who has not set
    one up. Deterministic first, model last.

    Returns ``(node, error)``: ``error`` is a human-readable reason when the text
    looked like JSON but no Recipe could be found, so the caller can say why
    instead of silently falling through to the model.
    """
    s = (text or "").strip()
    if not s:
        return None, ""

    if s[0] in "{[":
        try:
            data = json.loads(s)
        except json.JSONDecodeError as exc:
            return None, (f"That looks like JSON but could not be parsed: "
                          f"{exc.msg} at line {exc.lineno}, column {exc.colno}.")
        node = _find_recipe_node(data)
        if node:
            return node, ""
        found = _describe_types(data)
        return None, ("That JSON has no schema.org Recipe in it"
                      + (f" — found {found} instead." if found else "."))

    if "<" in s and "application/ld+json" in s:
        node = extract_jsonld_recipe(s)
        if node:
            return node, ""
        return None, "That markup has a JSON-LD block, but no Recipe inside it."

    return None, ""


def _describe_types(data) -> str:
    """The @type values present, for a 'no Recipe here' message."""
    types: list[str] = []

    def walk(v):
        if isinstance(v, dict):
            t = v.get("@type")
            for x in _as_list(t):
                if isinstance(x, str) and x not in types:
                    types.append(x)
            for sub in v.values():
                walk(sub)
        elif isinstance(v, list):
            for sub in v:
                walk(sub)

    walk(data)
    return ", ".join(types[:5])


def lint_payload(payload: dict) -> list[str]:
    """Non-fatal problems with an imported recipe, for the user to see.

    The import still succeeds — this is the difference between "it worked" and
    "it worked, and here is what was thin about the source".
    """
    from ...services import units

    warn: list[str] = []
    ings = payload.get("ingredients") or []
    steps = payload.get("steps") or []

    if not ings:
        warn.append("No ingredients were found.")
    if not steps:
        warn.append("No instructions were found.")
    if not payload.get("servings"):
        warn.append("No serving count — scaling will be unavailable until you set one.")

    unreadable = [i.get("display", "") for i in ings
                  if i.get("display") and units.parse_line(i["display"])["qty"] is None]
    if unreadable:
        warn.append(
            f"{len(unreadable)} of {len(ings)} ingredient lines have no readable "
            f"quantity (e.g. “{unreadable[0][:60]}”). They will scale and "
            f"consolidate poorly on a shopping list."
        )
    if not payload.get("totalMinutes"):
        warn.append("No timings — prep/cook time will be blank.")
    return warn


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
        "tags": [t for t in (_text(x) for x in _as_list(payload.get("tags"))) if t][:12],
        "notes": _text(payload.get("notes")),
    }


def _og_image(html: str, base: str) -> str:
    """Fallback recipe image: the page's OpenGraph/twitter image."""
    soup = BeautifulSoup(html, "html.parser")
    for sel in (
        {"property": "og:image"},
        {"name": "og:image"},
        {"name": "twitter:image"},
    ):
        tag = soup.find("meta", attrs=sel)
        if tag and tag.get("content"):
            return _abs_url(base, tag["content"])
    return ""


class UnsafeURLError(ValueError):
    """Raised when a URL targets a non-public / non-http destination."""


class UnsupportedPasteError(ValueError):
    """Pasted text was structured data, but not a schema.org Recipe.

    Typed so the endpoint can return this curated message as a 422 instead of
    the generic "no AI provider configured" 503 — the provider is not the
    problem, and saying it is sends people to the wrong settings page.
    """


_MAX_FETCH_BYTES = 3_000_000
_MAX_REDIRECTS = 5


def _assert_public_url(url: str) -> str:
    """Reject non-http(s) schemes and hosts that resolve to private ranges, and
    return the validated IP the connection MUST be pinned to.

    This is the SSRF guard: myMeal fetches user-supplied URLs server-side, and
    without this a group member could point it at localhost, the HA supervisor,
    a bundled Ollama, cloud metadata, or the LAN.

    Returning the resolved IP lets the caller connect to that exact address
    instead of re-resolving the hostname — closing the DNS-rebinding TOCTOU
    window where a name resolves public here but private at connect time. ALL
    resolved addresses are checked, so a multi-record rebind can't sneak one in.
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
    pinned_ip = ""
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
        if not pinned_ip:
            pinned_ip = info[4][0]
    if not pinned_ip:
        raise UnsafeURLError("could not resolve host")
    return pinned_ip


def pinned_get_args(url: str):
    """Validate ``url`` (SSRF) and return ``(pinned_url, headers, extensions)``
    for an httpx GET that connects to the ALREADY-VALIDATED IP — defeating DNS
    rebinding, since the name is resolved exactly once (in the guard) and the
    socket connects to that IP. TLS SNI + certificate verification still use the
    original hostname (via the sni_hostname extension), so HTTPS stays verified.
    """
    ip = _assert_public_url(url)
    u = httpx.URL(url)
    pinned = str(u.copy_with(host=ip))
    # Bracket an IPv6 literal in the Host header, and don't send an IP as SNI
    # (SNI is for hostnames; for an IP-literal source URL httpcore falls back to
    # the pinned IP as server_hostname, matching default behaviour).
    try:
        host_is_ip = bool(ipaddress.ip_address(u.host))
    except ValueError:
        host_is_ip = False
    host_for_header = f"[{u.host}]" if ":" in u.host else u.host
    host_header = host_for_header + (f":{u.port}" if u.port else "")
    extensions = ({"sni_hostname": u.host}
                  if u.scheme == "https" and not host_is_ip else {})
    return pinned, {"Host": host_header}, extensions


def _fetch(url: str) -> str:
    """Fetch a page, validating each redirect hop and capping the body size."""
    # Present as a normal browser: many recipe sites 403 an obvious bot UA.
    # (Big CDN-fronted sites may still block datacenter IPs; the AI/paste paths
    # cover those.)
    headers = {
        "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    current = url
    with httpx.Client(follow_redirects=False, timeout=20, headers=headers) as client:
        for _ in range(_MAX_REDIRECTS):
            pinned, host_hdr, ext = pinned_get_args(current)
            with client.stream("GET", pinned, headers=host_hdr, extensions=ext) as r:
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


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


_PARSE_SYSTEM = (
    "You parse cooking ingredient lines into structured parts — the machine-"
    "learning-style matching a recipe manager uses. For each input line give the "
    "numeric quantity (0 if none), the unit, the core food name, and any prep "
    "note. Never invent or drop lines; keep them in order."
)
_PARSE_HINT = (
    'Return JSON {"ingredients": [ {"display": string, "quantity": number, '
    '"unit": string, "food": string, "note": string} ]} — exactly one object '
    "per input line, in the same order."
)


def parse_ingredients(lines, provider: AIProvider) -> list[dict]:
    """Structure free-text ingredient lines into {display, quantity, unit, food,
    note} using the provider. On-demand (like Mealie's parser) — the caller
    reviews before saving. Never raises for a partial/misshaped model reply."""
    clean = [str(x).strip() for x in (lines or []) if str(x).strip()]
    if not clean:
        return []
    payload = provider.complete_json(
        _PARSE_HINT + "\n\nLines:\n" + "\n".join(clean), system=_PARSE_SYSTEM
    )
    parsed = _as_list(payload.get("ingredients") if isinstance(payload, dict) else payload)
    out = []
    aligned = len(parsed) == len(clean)  # only trust positional fallback if 1:1
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            continue
        display = _text(item.get("display"))
        if not display and aligned:
            display = clean[i]
        out.append({
            "display": display,
            "quantity": _to_float(item.get("quantity")),
            "unit": _text(item.get("unit")),
            "food": _text(item.get("food")),
            "note": _text(item.get("note")),
        })
    return out


_GENERATE_SYSTEM = (
    "You are an experienced home cook. Invent ONE practical, appealing recipe "
    "that matches the user's request, and return it as structured data. Use "
    "common household measurements, realistic times, and clear step-by-step "
    "instructions. Add a few short tags (cuisine, course, diet, method)."
)


def generate_recipe(
    prompt: str, provider: AIProvider, servings: int = 0, preferences: str = ""
) -> dict:
    """Draft a full recipe from a free-text idea ("a cozy vegetarian chili for
    4"). Returns the normalized payload — NOT saved — so the builder can prefill
    a form the user edits before saving. ``preferences`` is the household's saved
    diet/allergy summary, honoured when drafting."""
    ask = _SCHEMA_HINT + f"\n\nRequest: {prompt.strip()}"
    if servings:
        ask += f"\nTarget servings: {servings}"
    if preferences:
        ask += (
            f"\nHousehold preferences to honour (never include a listed "
            f"allergen): {preferences}"
        )
    payload = _normalize_ai(provider.complete_json(ask, system=_GENERATE_SYSTEM))
    if payload.get("name") == "Imported Recipe":  # the import default reads wrong here
        payload["name"] = "New recipe"
    if servings and not payload.get("servings"):
        payload["servings"] = servings
    return payload


_PHOTO_SYSTEM = (
    "You transcribe recipes from images — a photo of a recipe card, a cookbook "
    "page, a screenshot, or a handwritten note. Read the image and return the "
    "recipe as structured data. Transcribe the ingredients and steps faithfully; "
    "do NOT invent quantities or steps that are not shown. If the image is not a "
    "recipe, return a recipe with an empty name and no ingredients."
)


def recipe_from_image(image_b64: str, media_type: str, provider: AIProvider) -> dict:
    """Extract a recipe from a photo (base64). Returns the normalized payload —
    NOT saved. Raises ProviderError if the provider has no vision support."""
    prompt = _SCHEMA_HINT + "\n\nTranscribe the recipe shown in the attached image."
    payload = _normalize_ai(
        provider.complete_json_image(prompt, image_b64, media_type, system=_PHOTO_SYSTEM)
    )
    if payload.get("name") == "Imported Recipe":
        payload["name"] = "Scanned recipe"
    return payload


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
            payload = normalize_jsonld(node, source_url)
            if not payload.get("imageUrl"):
                payload["imageUrl"] = _og_image(html, source_url)
            payload["sourceUrl"] = source_url
            return payload

    # Pasted structured markup: already the shape we want, so parse it here and
    # never involve the model. This is what makes pasting a schema.org Recipe
    # work with no AI provider configured at all.
    if text.strip():
        node, why = extract_pasted_recipe(text)
        if node:
            payload = normalize_jsonld(node, source_url)
            if source_url:
                payload["sourceUrl"] = source_url
            return payload
        if why and provider is None:
            # It was JSON, just not a recipe. Say so, rather than reporting the
            # generic "no AI provider" — the provider was never the problem.
            raise UnsupportedPasteError(why)

    # AI path: raw text, or a page with no usable markup.
    if provider is None:
        raise ValueError("no AI provider available for recipe parsing")
    body = text.strip() or _visible_text(html)
    prompt = f"{_SCHEMA_HINT}\n\nSource text:\n\n{body}"
    payload = _normalize_ai(provider.complete_json(prompt, system=_IMPORT_SYSTEM))
    # The model can't see images; recover one from the fetched page's OG tags.
    if html and not payload.get("imageUrl"):
        payload["imageUrl"] = _og_image(html, source_url)
    if source_url:
        payload["sourceUrl"] = source_url
    return payload
