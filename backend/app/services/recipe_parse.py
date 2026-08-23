"""Deterministic recipe extraction from pasted text or HTML — no AI provider.

This is the "paste a recipe" path. It exists for the same reason
``extract_pasted_recipe`` does: the content is already in front of us, so
spending an LLM call to re-derive it is slower, costs money, can get it wrong,
and — worst — fails outright with "no AI provider configured" for anyone who
never set one up. Deterministic first, model last.

Three shapes arrive here, in descending order of how much the source tells us:

1. **Microdata** (``itemprop="recipeIngredient"``). Unlike JSON-LD this SURVIVES
   a browser copy: it lives in attributes on visible elements, whereas
   ``<script type="application/ld+json">`` is stripped by every browser when you
   select a page and hit copy. Mapped to a schema.org node so it goes through the
   one existing normalizer.
2. **Styled HTML** — what the clipboard actually holds after copying a rendered
   recipe: divs, spans, inline styles, a ``<style>`` block, and class names like
   ``ingredients``. Flattened to one line per element, keeping the list/heading
   structure that makes the text parseable, then handed to (3).
3. **A plain-text blob.** Parsed by structure: section headings, numbered steps,
   and — when a source has no headings at all — by classifying each line.

Anything this module can't confidently read returns ``None`` so the caller falls
through to the AI path unchanged.
"""
import re

from bs4 import BeautifulSoup

from . import units

# Section headings people actually write, including the ALL-CAPS and
# "INGREDIENTS:" variants. Matched on a short line only, so an instruction that
# happens to begin "Method: warm the milk" isn't mistaken for a heading.
_ING_HEAD = re.compile(
    r"^(?:ingredients?|you(?:'?ll)?\s+(?:will\s+)?need|what\s+you(?:'?ll)?\s+need"
    r"|shopping\s+list)\b\s*:?\s*$", re.I)
_STEP_HEAD = re.compile(
    r"^(?:method|instructions?|directions?|steps?|preparation|procedure"
    r"|how\s+to\s+make(?:\s+it)?|to\s+make)\b\s*:?\s*$", re.I)
# A sub-heading inside the ingredient list: "For the sauce:", "Topping:", or a
# bare "For the topping" with no colon. schema.org has no field for these but
# RecipeIngredient.section does, so they're captured rather than flattened away.
# The author's own wording is kept verbatim (minus the colon) — "For the drizzle"
# is how a recipe book labels it, and rewriting it to "drizzle" would be us
# editing their text for no gain.
_SUB_HEAD = re.compile(r"^(?:(?P<colon>.+?)\s*:|for\s+the\s+.+?)\s*$", re.I)

_STEP_NUMBER = re.compile(r"^\s*(?:step\s*)?(\d{1,2})\s*[.):\-–]\s+", re.I)
_HEADING_MAX = 60          # a heading is a short line, not a sentence

# "Serves 8", "Serves: 8-10", "8 servings", "Yield: 12 cookies", "Makes 24"
_SERVES = re.compile(
    r"\b(?:serves|servings?|yield|makes|portions?)\b\s*:?\s*(?P<n>\d+)"
    r"|(?P<n2>\d+)\s*(?:servings?|portions?)\b", re.I)
# "Prep 20 min", "Cook time: 1 hr 15 mins", "Total: 1 hour"
_DURATION_LABEL = {
    "prep": ("prep", "preparation", "active", "hands-on", "hands on"),
    "cook": ("cook", "cooking", "bake", "baking", "roast", "roasting", "grill"),
    "total": ("total", "ready in", "overall"),
}


def html_to_lines(html: str, limit: int = 12000) -> str:
    """Visible text, one line per element, with the junk removed.

    Block structure is what makes the text parseable — every ``<li>`` on its own
    line is the difference between an ingredient list and one run-on sentence —
    so this deliberately keeps newlines rather than collapsing to a paragraph.
    ``<style>`` goes with the other junk, which is what makes pasted CSS a no-op
    instead of garbage ingredients.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for junk in soup(["script", "style", "nav", "footer", "header", "noscript",
                      "form", "button", "select", "svg"]):
        junk.decompose()
    _mark_sections(soup)
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines() if ln.strip()]
    return "\n".join(lines)[:limit]


# class/id/itemprop values that name a recipe part. Copying a rendered page
# throws away JSON-LD but keeps these attributes, and they are a far stronger
# signal than guessing from the prose — a list of "a handful of parsley" style
# ingredients is otherwise indistinguishable from a list of short steps.
_CLASS_ING = re.compile(r"(?:^|[-_ ])(?:recipe)?ingredients?(?:[-_ ]|$)|recipeingredient", re.I)
_CLASS_STEP = re.compile(
    r"(?:^|[-_ ])(?:recipe)?(?:instructions?|directions?|method|steps)(?:[-_ ]|$)"
    r"|recipeinstructions?", re.I)


def _mark_sections(soup) -> None:
    """Insert a heading line before a list the markup itself labels.

    Emits the literal words the text parser already recognises, so there is one
    heading-detection rule rather than a second parallel mechanism. A duplicate
    heading is harmless — consecutive sections of the same kind are merged.
    """
    for el in soup.find_all(attrs={"class": True}) + soup.find_all(attrs={"id": True}) \
            + soup.find_all(attrs={"itemprop": True}):
        token = " ".join(filter(None, [
            " ".join(el.get("class") or []), el.get("id") or "", el.get("itemprop") or "",
        ]))
        if not token:
            continue
        # Only label a container, never an individual item: marking every <li>
        # would emit a heading per ingredient.
        if el.name in ("li", "span", "a", "strong", "em", "td"):
            continue
        word = "Ingredients" if _CLASS_ING.search(token) else (
            "Instructions" if _CLASS_STEP.search(token) else "")
        if word:
            el.insert_before(f"\n{word}\n")


def _itemprop_values(root, name: str) -> list[str]:
    """Text of every element carrying ``itemprop="<name>"`` under ``root``."""
    out = []
    for el in root.find_all(attrs={"itemprop": True}):
        props = str(el.get("itemprop") or "").lower().split()
        if name.lower() not in props:
            continue
        # <meta content>/<time datetime> carry the value in an attribute; a
        # visible element carries it as text.
        val = (el.get("content") or el.get("datetime") or el.get_text(" ")).strip()
        if val:
            out.append(re.sub(r"\s+", " ", val))
    return out


def extract_microdata_recipe(html: str) -> dict | None:
    """A schema.org Recipe expressed as microdata, as a JSON-LD-shaped node.

    Returned in node form on purpose: it then goes through the same
    ``normalize_jsonld`` as every other structured source, so ISO durations,
    yields and instruction flattening have exactly one implementation.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    scope = soup.find(attrs={"itemtype": re.compile(r"schema\.org/Recipe", re.I)})
    # No itemscope is fine: bare itemprop attributes are still unambiguous, and a
    # copied fragment often loses the wrapper that carried the itemtype.
    root = scope or soup
    ingredients = _itemprop_values(root, "recipeIngredient") \
        or _itemprop_values(root, "ingredients")     # pre-2017 schema.org name
    steps = _itemprop_values(root, "recipeInstructions")
    if not _enough(ingredients, steps):
        return None
    node = {
        "@type": "Recipe",
        "name": next(iter(_itemprop_values(root, "name")), ""),
        "description": next(iter(_itemprop_values(root, "description")), ""),
        "recipeIngredient": ingredients,
        "recipeInstructions": steps,
        "recipeYield": next(iter(_itemprop_values(root, "recipeYield")), ""),
        "prepTime": next(iter(_itemprop_values(root, "prepTime")), ""),
        "cookTime": next(iter(_itemprop_values(root, "cookTime")), ""),
        "totalTime": next(iter(_itemprop_values(root, "totalTime")), ""),
        "recipeCategory": _itemprop_values(root, "recipeCategory"),
        "recipeCuisine": _itemprop_values(root, "recipeCuisine"),
        "keywords": next(iter(_itemprop_values(root, "keywords")), ""),
    }
    img = soup.find(attrs={"itemprop": "image"})
    if img is not None:
        node["image"] = img.get("src") or img.get("content") or img.get_text(" ").strip()
    return node


def _enough(ingredients, steps) -> bool:
    """Whether we found enough to call this a recipe rather than guessing.

    Two ingredients, or one plus a step. A lone line is never enough — falling
    through to the model is better than inventing a one-ingredient recipe.
    """
    return len(ingredients) >= 2 or (len(ingredients) >= 1 and len(steps) >= 1)


def _looks_like_ingredient(line: str) -> bool:
    """Whether a line reads as an amount of something, not as an instruction.

    Used only when the source gives no headings at all. The measurement parser is
    the primary signal — it already knows every unit spelling and fraction form
    the app supports, so this stays one vocabulary rather than a second list.
    """
    s = line.strip()
    if not s or len(s) > 120:
        return False              # instructions are sentences; ingredients aren't
    if s.endswith(":"):
        return False              # a trailing colon is a heading ("For the sauce:")
    p = units.parse_line(s)
    if p["qty"] is not None:
        # A leading number is strong — unless the line is a sentence that merely
        # starts with one ("2 minutes before serving, add the cream").
        return not (s.rstrip().endswith(".") and len(s.split()) > 12)
    # No number, but a bare unit or a very short noun phrase still reads as an
    # ingredient ("Salt and pepper", "Olive oil").
    return len(s.split()) <= 5 and not s.rstrip().endswith(".")


def _is_sub_heading(line: str) -> bool:
    """A "For the sauce:" style label inside the ingredient list.

    A main heading is NOT one: "Ingredients:" also ends in a colon, and treating
    it as a sub-heading would name every following row after it.
    """
    s = line.strip()
    if len(s) > _HEADING_MAX or _ING_HEAD.match(s) or _STEP_HEAD.match(s):
        return False
    if not _SUB_HEAD.match(s):
        return False
    # "1 cup sugar:" is not a heading. Anything with an amount in it is an
    # ingredient that happens to be punctuated oddly.
    return units.parse_line(s.rstrip(":"))["qty"] is None


def _strip_step_number(line: str) -> str:
    return _STEP_NUMBER.sub("", line, count=1).strip()


def _parse_meta(lines: list[str]) -> dict:
    """Servings and prep/cook/total minutes from the lines above the ingredients."""
    meta = {"servings": 0, "prepMinutes": 0, "cookMinutes": 0, "totalMinutes": 0,
            "recipeYield": ""}
    for ln in lines:
        m = _SERVES.search(ln)
        if m and not meta["servings"]:
            meta["servings"] = int(m.group("n") or m.group("n2") or 0)
            meta["recipeYield"] = ln.strip()[:120]
        for key, words in _DURATION_LABEL.items():
            if meta[f"{key}Minutes"]:
                continue
            for w in words:
                # The label and its duration must be adjacent, so "Prep 20 min"
                # matches but a sentence mentioning both words does not.
                hit = re.search(
                    rf"\b{re.escape(w)}\b[^.\n\d]{{0,12}}?(?P<d>\d[\d\s./]*"
                    rf"(?:h(?:ours?|rs?)?|m(?:in(?:ute)?s?)?)"
                    rf"(?:\s*\d+\s*m(?:in(?:ute)?s?)?)?)", ln, re.I)
                if hit:
                    meta[f"{key}Minutes"] = _minutes(hit.group("d"))
                    break
    if not meta["totalMinutes"]:
        meta["totalMinutes"] = meta["prepMinutes"] + meta["cookMinutes"]
    return meta


def _minutes(text: str) -> int:
    """"1 hr 15 min" / "90 minutes" / "1.5 hours" -> whole minutes."""
    total = 0.0
    for num, unit_word in re.findall(r"(\d+(?:[./]\d+)?)\s*([a-z]*)", text.lower()):
        try:
            n = float(num) if "/" not in num else (
                float(num.split("/")[0]) / float(num.split("/")[1]))
        except (ValueError, ZeroDivisionError):
            continue
        if unit_word.startswith("h"):
            total += n * 60
        elif unit_word.startswith("m") or not unit_word:
            total += n
    return int(round(total))


def _payload(*, name, description="", ingredients, steps, meta, notes="") -> dict:
    """The import payload shape.

    Mirrors ``normalize_jsonld``'s keys exactly; ``test_payload_shapes_match``
    asserts that, so the two builders cannot drift apart.
    """
    from .cooking import parse_temperature
    return {
        "name": name or "Imported Recipe",
        "description": description,
        "recipeYield": meta.get("recipeYield", ""),
        "servings": meta.get("servings", 0),
        "prepMinutes": meta.get("prepMinutes", 0),
        "cookMinutes": meta.get("cookMinutes", 0),
        "totalMinutes": meta.get("totalMinutes", 0),
        "cookTemperatureC": parse_temperature(" ".join(s["text"] for s in steps)),
        "ingredients": ingredients,
        "steps": steps,
        "tags": [],
        "imageUrl": "",
        "notes": notes,
    }


def parse_recipe_text(text: str) -> dict | None:
    """Parse a pasted recipe blob, or None when it isn't confidently a recipe.

    Two strategies, in order:

    * **Headed** — the blob has "Ingredients" / "Method" headings, so the split
      is explicit and sub-headings ("For the sauce:") become ingredient sections.
    * **Unheaded** — no headings at all, so each line is classified. Ingredients
      cluster at the top of a recipe, so the first run of amount-like lines is
      the ingredient list and the prose after it is the method.
    """
    raw = [ln.strip() for ln in (text or "").replace("\r\n", "\n").splitlines()]
    lines = [ln for ln in raw if ln]
    if len(lines) < 3:
        return None

    ing_idx = next((i for i, ln in enumerate(lines)
                    if len(ln) <= _HEADING_MAX and _ING_HEAD.match(ln)), None)
    step_idx = next((i for i, ln in enumerate(lines)
                     if len(ln) <= _HEADING_MAX and _STEP_HEAD.match(ln)), None)

    if ing_idx is not None or step_idx is not None:
        got = _parse_headed(lines, ing_idx, step_idx)
    else:
        got = _parse_unheaded(lines)
    if got is None:
        return None
    name, ingredients, steps, head_lines = got
    if not _enough(ingredients, steps):
        return None
    return _payload(name=name, ingredients=ingredients, steps=steps,
                    meta=_parse_meta(head_lines))


def _parse_headed(lines, ing_idx, step_idx):
    """Split on explicit headings. Either heading may be absent."""
    first = min(i for i in (ing_idx, step_idx) if i is not None)
    head_lines = lines[:first]
    name = next((ln for ln in head_lines if len(ln) <= 120), "")

    if ing_idx is None:
        ing_body, step_body = [], lines[step_idx + 1:]
    elif step_idx is None:
        ing_body, step_body = lines[ing_idx + 1:], []
    elif ing_idx < step_idx:
        ing_body, step_body = lines[ing_idx + 1:step_idx], lines[step_idx + 1:]
    else:
        # Method printed above the ingredients — rare, but real in older books.
        step_body, ing_body = lines[step_idx + 1:ing_idx], lines[ing_idx + 1:]

    return name, _ingredient_rows(ing_body), _step_rows(step_body), head_lines


def _parse_unheaded(lines):
    """No headings: classify. Returns None when no ingredient run is found."""
    name = lines[0] if len(lines[0]) <= 120 else ""
    body = lines[1:]
    flags = [_looks_like_ingredient(ln) for ln in body]
    try:
        start = flags.index(True)
    except ValueError:
        return None
    # The ingredient run ends at the second consecutive non-ingredient line, so a
    # single wrapped ingredient or a "For the sauce:" sub-heading doesn't end it.
    end = len(body)
    misses = 0
    for i in range(start, len(body)):
        if flags[i]:
            misses = 0
            continue
        misses += 1
        if misses >= 2:
            end = i - 1
            break
    run = body[start:end]
    # With no headings to go on, SOMETHING has to be measurable or this is just
    # prose: "Hello / How are you / See you soon" is three short lines that end
    # without a full stop, which is otherwise indistinguishable from
    # "Salt / Olive oil / Butter". Refusing here sends it to the model, which is
    # the right answer for text we genuinely cannot read.
    if not any(units.parse_line(ln)["qty"] is not None for ln in run):
        return None
    return (name, _ingredient_rows(run), _step_rows(body[end:]),
            [lines[0]] + body[:start])


def _ingredient_rows(body: list[str]) -> list[dict]:
    """Ingredient lines -> rows, carrying any sub-heading as ``section``."""
    rows: list[dict] = []
    section = ""
    for ln in body:
        if _is_sub_heading(ln):
            section = ln.rstrip(":").strip()
            continue
        display = units.strip_list_marker(ln).strip()
        if not display:
            continue
        row = {"display": display}
        if section:
            row["section"] = section[:255]
        rows.append(row)
    return rows


def _step_rows(body: list[str]) -> list[dict]:
    """Instruction lines -> steps, with any "1." / "Step 2:" prefix removed.

    Lines are joined when a source hard-wraps mid-sentence: a fragment that does
    not start a new numbered step and does not begin with a capital is a
    continuation, not a step of its own.
    """
    steps: list[dict] = []
    for ln in body:
        numbered = bool(_STEP_NUMBER.match(ln))
        text = _strip_step_number(ln)
        if not text:
            continue
        cont = (steps and not numbered and text[:1].islower())
        if cont:
            steps[-1]["text"] = f"{steps[-1]['text']} {text}".strip()
        else:
            steps.append({"title": "", "text": text})
    return steps


def parse_pasted(text: str) -> dict | None:
    """Deterministic read of whatever was pasted, or None to fall through to AI.

    Order matters: microdata beats guessing from prose, and HTML has to become
    line-structured text before the text parser can see the list it contains.
    """
    s = (text or "").strip()
    if not s:
        return None
    if _looks_like_html(s):
        node = extract_microdata_recipe(s)
        if node:
            from .ai.recipe_import import normalize_jsonld
            return normalize_jsonld(node)
        return parse_recipe_text(html_to_lines(s))
    return parse_recipe_text(s)


def _looks_like_html(s: str) -> bool:
    """A tag, not merely an angle bracket — "<1 cup" or "a < b" isn't markup."""
    return bool(re.search(r"<\s*/?\s*[a-zA-Z][a-zA-Z0-9]*[\s/>]", s))
