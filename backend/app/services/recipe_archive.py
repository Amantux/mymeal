"""Bulk import from other recipe managers' export files — the migration path.

For a self-hoster, the biggest "data source" isn't an API: it's the collection
they already built in another app. This reads the export formats of the three
they most likely came from, entirely offline — no keys, no network, no model:

* **Paprika** ``.paprikarecipes`` — a ZIP of ``.paprikarecipe`` entries, each a
  GZIPPED JSON object (name, newline-joined ingredients/directions, times,
  a base64 photo).
* **Tandoor** — a ZIP of per-recipe ZIPs, each holding ``recipe.json`` with
  steps that EMBED their ingredients (amount/unit{name}/food{name}).
* **Mealie** — a ZIP (or bare ``.json``) of per-recipe JSON in Mealie's own
  schema-org-ish shape (recipeIngredient as objects, recipeInstructions as
  [{text}]).
* **Generic fallbacks** — a schema.org Recipe JSON (through the same
  ``normalize_jsonld`` as every other structured source) and plain ``.txt``/
  ``.md`` (through the deterministic text parser).

Per-entry failures never abort the batch: each file lands in ``created`` or in
``skipped`` with a reason, because a 300-recipe migration that dies at entry 217
with a stack trace is worse than one that finishes and tells you about 4 skips.

Zip handling is defensive by construction — an export is a file someone
downloaded and re-uploaded, not trusted input. Entry count, per-entry
uncompressed size, and total uncompressed size are all capped BEFORE reading
(zip-bomb guards), nested archives only one level deep (Tandoor's shape), and
entry names are never used as filesystem paths.
"""
from __future__ import annotations

import gzip
import io
import json
import logging
import zipfile

_LOGGER = logging.getLogger("mymeal.recipe_archive")

MAX_ENTRIES = 500                      # recipes per archive
MAX_ENTRY_BYTES = 4 * 1024 * 1024      # one recipe JSON is KBs; 4 MB is a photo
MAX_TOTAL_BYTES = 120 * 1024 * 1024    # decompressed, across the whole archive
MAX_PHOTO_BYTES = 8 * 1024 * 1024


def _minutes(value) -> int:
    """Accept an ISO duration, a bare number, or "45 min" prose."""
    from .ai.recipe_import import _iso_duration_to_minutes
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    s = str(value).strip()
    if not s:
        return 0
    if s.upper().startswith(("P", "PT")):
        return _iso_duration_to_minutes(s)
    import re
    m = re.search(r"\d+", s)
    mins = int(m.group()) if m else 0
    return mins * 60 if "hour" in s.lower() or s.lower().rstrip("s").endswith("hr") else mins


def _payload(*, name, description="", servings=0, prep=0, cook=0,
             ingredients, steps, tags=(), notes="", source_url="") -> dict:
    from .cooking import parse_temperature
    steps = [s for s in steps if s.get("text")]
    out = {
        "name": (str(name or "").strip() or "Imported Recipe")[:200],
        "description": str(description or "").strip(),
        "recipeYield": "",
        "servings": _first_int(servings) if isinstance(servings, str)
        else max(0, int(servings or 0)),
        "prepMinutes": prep,
        "cookMinutes": cook,
        "totalMinutes": prep + cook,
        "cookTemperatureC": parse_temperature(" ".join(s["text"] for s in steps)),
        "ingredients": [i for i in ingredients if i.get("display")],
        "steps": steps,
        "tags": [str(t).strip()[:60] for t in tags if str(t).strip()][:12],
        "imageUrl": "",
        "notes": str(notes or "").strip(),
    }
    if source_url:
        out["sourceUrl"] = str(source_url).strip()
    return out


def _first_int(value) -> int:
    import re
    m = re.search(r"\d+", str(value or ""))
    return int(m.group()) if m else 0


def _lines_to_rows(text: str) -> list[dict]:
    return [{"display": ln.strip()} for ln in str(text or "").splitlines() if ln.strip()]


def _lines_to_steps(text: str) -> list[dict]:
    from .recipe_parse import strip_step_number
    return [{"title": "", "text": strip_step_number(ln)[:2000]}
            for ln in str(text or "").replace("\r\n", "\n").split("\n") if ln.strip()]


# --- The three named formats -------------------------------------------------

def parse_paprika(data: dict) -> dict:
    """One decompressed ``.paprikarecipe`` JSON object."""
    return _payload(
        name=data.get("name"),
        description=data.get("description"),
        servings=_first_int(data.get("servings")),
        prep=_minutes(data.get("prep_time")),
        cook=_minutes(data.get("cook_time")),
        ingredients=_lines_to_rows(data.get("ingredients")),
        steps=_lines_to_steps(data.get("directions")),
        tags=data.get("categories") or (),
        notes=data.get("notes"),
        source_url=data.get("source_url"),
    )


def parse_tandoor(data: dict) -> dict:
    """Tandoor's ``recipe.json``: ingredients live INSIDE each step."""
    ingredients: list[dict] = []
    steps: list[dict] = []
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for ing in step.get("ingredients") or []:
            if not isinstance(ing, dict):
                continue
            amount = ing.get("amount") or 0
            unit = ((ing.get("unit") or {}).get("name") or "") if isinstance(
                ing.get("unit"), dict) else ""
            food = ((ing.get("food") or {}).get("name") or "") if isinstance(
                ing.get("food"), dict) else ""
            note = str(ing.get("note") or "").strip()
            parts = []
            if amount:
                # Tandoor stores floats; 2.0 should read "2".
                parts.append(f"{amount:g}" if isinstance(amount, float) else str(amount))
            parts += [p for p in (unit, food) if p]
            display = " ".join(parts).strip()
            if note and display:
                display = f"{display}, {note}"
            if display:
                ingredients.append({"display": display})
        text = str(step.get("instruction") or "").strip()
        if text:
            steps.append({"title": str(step.get("name") or "").strip()[:120],
                          "text": text[:4000]})
    return _payload(
        name=data.get("name"),
        description=data.get("description"),
        servings=_first_int(data.get("servings")),
        prep=_minutes(data.get("working_time")),
        cook=_minutes(data.get("waiting_time")),
        ingredients=ingredients,
        steps=steps,
        tags=[(k.get("name") if isinstance(k, dict) else k)
              for k in (data.get("keywords") or [])],
        source_url=data.get("source_url"),
    )


def parse_mealie(data: dict) -> dict:
    """Mealie's per-recipe JSON (schema-org-ish, ingredients as objects)."""
    ingredients = []
    for ing in data.get("recipeIngredient") or []:
        if isinstance(ing, str):
            display = ing.strip()
        elif isinstance(ing, dict):
            # display/originalText carry the human line; fall back to parts.
            display = str(ing.get("display") or ing.get("originalText") or "").strip()
            if not display:
                qty = ing.get("quantity") or ""
                unit = ((ing.get("unit") or {}).get("name") or "") if isinstance(
                    ing.get("unit"), dict) else str(ing.get("unit") or "")
                food = ((ing.get("food") or {}).get("name") or "") if isinstance(
                    ing.get("food"), dict) else str(ing.get("food") or "")
                note = str(ing.get("note") or "").strip()
                display = " ".join(str(p).strip() for p in (qty, unit, food) if p).strip()
                if note:
                    display = f"{display}, {note}" if display else note
        else:
            continue
        if display:
            ingredients.append({"display": display})
    steps = []
    for st in data.get("recipeInstructions") or []:
        text = (st.get("text") if isinstance(st, dict) else str(st or "")) or ""
        if str(text).strip():
            steps.append({"title": (st.get("title") or "")[:120] if isinstance(st, dict) else "",
                          "text": str(text).strip()[:4000]})
    return _payload(
        name=data.get("name"),
        description=data.get("description"),
        servings=_first_int(data.get("recipeServings") or data.get("recipeYield")),
        prep=_minutes(data.get("prepTime")),
        cook=_minutes(data.get("performTime") or data.get("cookTime")),
        ingredients=ingredients,
        steps=steps,
        tags=[(t.get("name") if isinstance(t, dict) else t)
              for t in (data.get("tags") or [])],
        notes="\n".join(str(n.get("text") if isinstance(n, dict) else n or "")
                        for n in (data.get("notes") or [])),
        source_url=data.get("orgURL") or data.get("org_url"),
    )


def parse_json_entry(data) -> dict | None:
    """Route one parsed JSON object to the right format parser, or None."""
    if not isinstance(data, dict):
        # A Mealie/handmade export can be a bare LIST of recipes; the caller
        # iterates those itself.
        return None
    # schema.org first: exact, and shared with every other structured source.
    from .ai.recipe_import import _find_recipe_node, normalize_jsonld
    node = _find_recipe_node(data)
    if node is not None and not _looks_like_mealie(node):
        return normalize_jsonld(node)
    if _looks_like_mealie(data):
        return parse_mealie(data)
    if isinstance(data.get("steps"), list) and any(
            isinstance(s, dict) and "instruction" in s for s in data["steps"]):
        return parse_tandoor(data)
    if "ingredients" in data and "directions" in data:
        return parse_paprika(data)
    return None


def _looks_like_mealie(data: dict) -> bool:
    """Mealie rows LOOK like schema.org (they carry @type-free recipeIngredient)
    but the ingredients are OBJECTS with food/unit/display — sending those
    through normalize_jsonld's _text() flattens them into keyword soup."""
    ings = data.get("recipeIngredient")
    return isinstance(ings, list) and any(
        isinstance(i, dict) and ("display" in i or "food" in i or "originalText" in i)
        for i in ings)


# --- Archive walking ---------------------------------------------------------

def _safe_read(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes | None:
    """Read one entry with the size caps enforced BEFORE decompression trusts
    the header, and again while reading (a lying header is the attack)."""
    if info.file_size > MAX_ENTRY_BYTES:
        return None
    with zf.open(info) as fh:
        data = fh.read(MAX_ENTRY_BYTES + 1)
    return None if len(data) > MAX_ENTRY_BYTES else data


def extract_payloads(filename: str, blob: bytes) -> tuple[list[dict], list[dict]]:
    """Every recipe payload found in an uploaded export file.

    Returns ``(payloads, skipped)`` where skipped entries carry a human reason.
    Never raises on malformed content — an unreadable entry is a skip, not a
    crash.
    """
    payloads: list[dict] = []
    skipped: list[dict] = []
    budget = {"total": 0, "count": 0}

    def add(name: str, payload: dict | None, why: str = "") -> None:
        if budget["count"] >= MAX_ENTRIES:
            return
        if payload and payload.get("ingredients"):
            budget["count"] += 1
            payload["_entry"] = name
            payloads.append(payload)
        else:
            skipped.append({"entry": name, "reason": why or "no recipe found in it"})

    def eat_json(name: str, raw: bytes) -> None:
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            add(name, None, "not valid JSON")
            return
        items = data if isinstance(data, list) else [data]
        for i, item in enumerate(items[:MAX_ENTRIES]):
            label = name if len(items) == 1 else f"{name}[{i}]"
            got = parse_json_entry(item)
            add(label, got, "JSON, but not a recipe shape this importer knows")

    def eat_entry(name: str, raw: bytes, depth: int) -> None:
        budget["total"] += len(raw)
        if budget["total"] > MAX_TOTAL_BYTES:
            skipped.append({"entry": name, "reason": "archive size limit reached"})
            return
        low = name.lower()
        if low.endswith(".paprikarecipe"):
            try:
                raw = gzip.decompress(raw[:MAX_ENTRY_BYTES])
            except OSError:
                add(name, None, "not the gzip format Paprika writes")
                return
            eat_json(name, raw)
        elif low.endswith((".json",)):
            eat_json(name, raw)
        elif low.endswith((".txt", ".md")):
            from .recipe_parse import parse_recipe_text
            add(name, parse_recipe_text(raw.decode("utf-8", "replace")),
                "text, but not readable as a recipe")
        elif low.endswith((".zip", ".paprikarecipes")) and depth < 1:
            eat_zip(name, raw, depth + 1)
        elif low.endswith((".jpg", ".jpeg", ".png", ".webp")):
            pass          # sibling images; recipes are imported without them
        else:
            skipped.append({"entry": name, "reason": "unrecognised file type"})

    def eat_zip(name: str, raw: bytes, depth: int) -> None:
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            add(name, None, "not a readable zip archive")
            return
        infos = [i for i in zf.infolist() if not i.is_dir()][:MAX_ENTRIES]
        for info in infos:
            data = _safe_read(zf, info)
            if data is None:
                skipped.append({"entry": info.filename,
                                "reason": "entry larger than the per-file limit"})
                continue
            eat_entry(info.filename, data, depth)

    low = (filename or "").lower()
    if low.endswith((".zip", ".paprikarecipes")):
        eat_zip(filename, blob, 0)
    else:
        eat_entry(filename or "upload", blob, 0)
    return payloads, skipped
