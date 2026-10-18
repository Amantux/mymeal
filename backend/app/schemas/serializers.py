"""Serialization helpers producing camelCase JSON for the SPA + API clients."""
import json
from datetime import datetime, date

from ..services import food_resolve, step_structure, units


def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, (datetime, date)):
        return dt.isoformat()
    return dt


def safe_url(u):
    """Only emit http(s) URLs to clients. Blanks javascript:/data:/etc. so a
    stored value can never become an XSS vector when rendered as an href —
    especially on the unauthenticated public share page."""
    u = (u or "").strip()
    return u if u[:7].lower() == "http://" or u[:8].lower() == "https://" else ""


def group_out(g):
    return {
        "id": g.id,
        "name": g.name,
        "createdAt": iso(g.created_at),
        "updatedAt": iso(g.updated_at),
    }


def user_out(u):
    return {
        "id": u.id,
        "name": u.name,
        "email": u.email,
        "isOwner": u.is_owner,
        "isSuperuser": u.is_superuser,
        "groupId": u.group_id,
        "groupName": u.group.name if u.group else None,
    }


def food_out(f):
    return {
        "id": f.id,
        "name": f.name,
        "pluralName": f.plural_name,
        # aliases_of, not a hand-rolled split: this was the FOURTH divergent
        # parser, and it mangled JSON-string rows onto the wire as
        # ['["eggplant"', '"brinjal"]'].
        "aliases": food_resolve.aliases_of(f),
        "aisle": f.aisle,
        "description": f.description,
    }


def unit_out(u):
    return {
        "id": u.id,
        "name": u.name,
        "pluralName": u.plural_name,
        "abbreviation": u.abbreviation,
    }


def category_out(c):
    return {
        "id": c.id,
        "name": c.name,
        "slug": c.slug,
        "description": c.description,
    }


def tag_out(t):
    return {"id": t.id, "name": t.name, "slug": t.slug, "color": t.color}


def ingredient_out(ing):
    parsed = units.annotations(ing.display or "")
    return {
        "id": ing.id,
        "display": ing.display,
        "quantity": ing.quantity,
        # Display-ready amount, so the SPA renders the quantity as its own
        # typographic column without re-deriving anything. `quantity` is a raw
        # float (0.6667 after scaling), which must never reach a cook's eye;
        # format_qty turns it back into "2/3". Kept alongside `display` rather
        # than replacing it — `display` stays the source of truth and is still
        # what unstructured/legacy rows and the weight annotation rely on.
        # A lossless split of `display` into a scannable amount column plus the
        # rest, so the SPA never rebuilds the ingredient from food.name — that
        # rebuild drops whatever was canonicalized away on save ("granulated
        # sugar" -> the "sugar" Food). See units.split_amount for why one function
        # has to decide both halves.
        **dict(zip(("amountText", "unitText", "restText"),
                   units.split_amount(ing.display, ing.quantity))),
        # An alternative WAY TO MEASURE the same amount, lifted out of the food
        # name ("1 g Garlic Powder or ½ tsp") — a cook with no scale needs the
        # teaspoon. Derived, never stored: it restates an amount already in
        # `quantity`, so a column would be a second truth for scaling to keep in
        # step. `optional` is lifted out the same way, so the word stops riding
        # along in the food name and into the Food catalog.
        # See units.annotations for what each one means and why the view cannot
        # derive them itself.
        "altText": parsed["alt"],
        "optional": parsed["optional"],
        "unmeasured": parsed["unmeasured"],
        "note": ing.note,
        # The variety, split off the food. Separate from `note`, which is
        # preparation.
        "qualifier": ing.qualifier,
        "section": ing.section,
        # The author declared this line to be prose, so it is deliberately
        # unstructured rather than merely unparsed. The editor needs this to
        # reproduce the free-text lane on the next edit — without it the line
        # looks like any other food-less legacy row and gets re-parsed back into
        # structure, minting the Food this flag exists to prevent.
        "freeText": ing.free_text,
        "position": ing.position,
        "unit": unit_out(ing.unit) if ing.unit else None,
        "food": food_out(ing.food) if ing.food else None,
        "refRecipe": ({"id": ing.ref_recipe.id, "name": ing.ref_recipe.name,
                       "slug": ing.ref_recipe.slug} if ing.ref_recipe else None),
    }


def step_out(s):
    return {"id": s.id, "position": s.position, "title": s.title, "text": s.text}


def version_out(v, include_snapshot=False):
    """A recipe version/experiment. The snapshot (full recipe payload) is only
    included on the detail view; never leaks another group's data (callers are
    group-scoped)."""
    d = {
        "id": v.id,
        "kind": v.kind,
        "status": v.status,
        "label": v.label,
        "note": v.note,
        "rating": v.rating,
        "feedback": v.feedback,
        "parentVersionId": v.parent_version_id,
        "createdAt": iso(v.created_at),
    }
    if include_snapshot:
        d["snapshot"] = json.loads(v.snapshot) if v.snapshot else None
    return d


def recipe_summary(r):
    # Local import, matching the other service imports in this module.
    from ..services.cooking import format_temperature

    _media = [recipe_media_out(v) for v in r.videos]
    return {
        "id": r.id,
        "name": r.name,
        "slug": r.slug,
        "description": r.description,
        "cookTemperatureC": r.cook_temperature_c,
        "cookTemperature": format_temperature(r.cook_temperature_c),
        "image": f"/api/v1/recipes/{r.id}/image" if r.image else None,
        # `media` is the name; `videos` is the same list under its former key,
        # kept for API clients written against it. Same objects, so the only
        # cost is JSON bytes on a field that is usually empty.
        "media": _media,
        "videos": _media,
        "servings": r.servings,
        "recipeYield": r.recipe_yield,
        "totalMinutes": r.total_minutes,
        "rating": r.rating,
        "isFavorite": r.is_favorite,
        "tags": [tag_out(t) for t in r.tags],
        "categories": [category_out(c) for c in r.categories],
        "createdAt": iso(r.created_at),
        "updatedAt": iso(r.updated_at),
    }


def mealplan_entry_out(e):
    return {
        "id": e.id,
        "date": iso(e.date),
        "mealType": e.meal_type,
        "title": e.title,
        "notes": e.notes,
        "servings": e.servings,
        "recipeId": e.recipe_id,
        "recipe": recipe_summary(e.recipe) if e.recipe else None,
        "createdAt": iso(e.created_at),
    }



def shopping_item_out(i):
    return {
        "id": i.id,
        "display": i.display,
        "quantity": i.quantity,
        "unit": i.unit,
        "aisle": i.aisle,
        "checked": i.checked,
        "position": i.position,
        "foodId": i.food_id,
    }


def shopping_list_out(sl):
    return {
        "id": sl.id,
        "name": sl.name,
        "items": [shopping_item_out(i) for i in sl.items],
        "createdAt": iso(sl.created_at),
        "updatedAt": iso(sl.updated_at),
    }


def chat_message_out(m):
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "toolTrace": json.loads(m.tool_trace) if m.tool_trace else [],
        "createdAt": iso(m.created_at),
    }


def chat_session_summary(s):
    return {
        "id": s.id,
        "title": s.title,
        "createdAt": iso(s.created_at),
        "updatedAt": iso(s.updated_at),
    }


def chat_session_out(s):
    data = chat_session_summary(s)
    data["messages"] = [chat_message_out(m) for m in s.messages]
    return data


def _nutrition_dict(recipe):
    if not recipe.nutrition:
        return {}
    try:
        val = json.loads(recipe.nutrition)
    except (ValueError, TypeError):
        return {}
    return val if isinstance(val, dict) else {}


def _as_number(value):
    """Coerce a nutrition value to float — accepts ints/floats and numeric strings
    (the AI parser sometimes stores "100"); rejects bools and non-numeric. None if
    not a number, so the caller skips it."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (ValueError, AttributeError):
            return None
    return None


def _effective_nutrition(r):
    """The recipe's own nutrition plus each linked component's nutrition, scaled by
    the component multiplier (cycle-guarded / capped via iter_recipe_tree). Only
    numeric values are summed; returns None if nothing numeric was found."""
    from ..services.components import iter_recipe_tree

    total: dict[str, float] = {}
    found = False
    for node, mult in iter_recipe_tree(r):
        for key, value in _nutrition_dict(node).items():
            num = _as_number(value)   # coerce numeric strings ("100") like the raw field
            if num is None:
                continue
            total[key] = total.get(key, 0.0) + num * mult
            found = True
    if not found:
        return None
    return {k: round(v, 2) for k, v in total.items()}


#: How much a stored figure can be stood behind, weakest first. A recipe that
#: links components inherits the WEAKEST label among everything summed into its
#: total: one model-guessed sub-recipe makes the whole number a guess, and
#: saying otherwise would be the same "0 means both zero and unknown" dishonesty
#: this feature exists to end. Mirrors foodcore's weakest-link confidence rule.
_SOURCE_RANK = {"estimated": 0, "manual": 1, "computed": 2}


def _effective_nutrition_source(r):
    """The weakest source label among every node contributing a number, or None
    when nothing numeric was found OR any contributor is unlabelled.

    Written as collect-then-decide rather than a running minimum: the earlier
    version tracked the weakest in-place and its "unlabelled" branch turned out
    to be unreachable for a recipe with no components, which a mutation test
    caught. Three plain steps are harder to get subtly wrong than one clever
    loop, and this one is read by anybody auditing where a number came from.
    """
    from ..services.components import iter_recipe_tree

    labels = []
    for node, _mult in iter_recipe_tree(r):
        data = _nutrition_dict(node)
        if not any(_as_number(v) is not None for v in data.values()):
            continue
        labels.append(data.get("source"))

    if not labels:
        return None
    # One unlabelled contributor makes the TOTAL unknown: it was stored before
    # provenance existed, and a sum is only as trustworthy as its vaguest part.
    if any(label not in _SOURCE_RANK for label in labels):
        return None
    return min(labels, key=lambda label: _SOURCE_RANK[label])


def _effective_nutrition_completeness(r):
    """Ingredient-mass coverage behind a COMPUTED figure, as a percentage.

    Only meaningful for arithmetic — a model's guess has no coverage to report —
    and only for a recipe standing alone: summing coverage across components
    would need their masses, which this serializer does not have.
    """
    data = _nutrition_dict(r)
    if data.get("source") != "computed":
        return None
    raw = data.get("completeness")
    if isinstance(raw, dict):
        raw = raw.get("overall", raw.get("mass"))
    num = _as_number(raw)
    if num is None:
        return None
    return round(num * 100, 1) if num <= 1 else round(num, 1)


# ---------------------------------------------------------------------------
# Recipe safety: allergen states and dietary compatibility
#
# EVERY field of every piece of evidence reaches the wire, and that is the whole
# point of writing these three functions out longhand instead of reaching for a
# generic dataclass-to-camelCase walker.
#
# The cautionary tale is directly above. `_effective_nutrition` sums only values
# that pass `_as_number`, so the non-numeric keys a nutrition record carries —
# `source`, `completeness`, the provenance of the figure — were silently dropped
# one layer below the UI. Nobody noticed until somebody went looking, and the
# fix was two extra top-level fields (`nutritionSource`,
# `nutritionCompleteness`) bolted on afterwards.
#
# Here the stakes are higher: a verdict whose evidence did not survive
# serialization is a bare word like "unknown" with nothing behind it, and
# foodcore raises rather than construct one. Dropping it on the way out would
# reproduce by accident exactly the thing the engine refuses to do on purpose.
# `test_recipe_safety.py` asserts field-by-field that nothing is lost.
#
# There is also no boolean in this payload that a client could read as "safe".
# `state` is one of the four words, `stateText` is the engine's OWN phrasing of
# it (so "(not a safety claim)" travels with the data instead of being
# reinvented in the UI), and `certified` is foodcore's narrowly-defined dietary
# term, never an allergen one. No `safe`, no `free`, no `ok`.
# ---------------------------------------------------------------------------

def _provenance_out(p):
    """A foodcore `Provenance`, or None. All seven fields; `confidence` is a
    number and `method`/`license` are the claims that qualify it."""
    if p is None:
        return None
    return {
        "source": p.source,
        "sourceId": p.source_id,
        "retrieved": p.retrieved,
        "method": p.method,
        "version": p.version,
        "confidence": p.confidence,
        "license": p.license,
    }


def _evidence_out(e):
    """One `Evidence`/`DietEvidence` row. The two types have identical fields by
    design, so one function serializes both; `contributes` carries whichever
    state enum its own engine uses."""
    return {
        "ingredient": e.ingredient,
        "matchedTerm": e.matched_term,
        "ruleId": e.rule_id,
        "ruleVersion": e.rule_version,
        "matchKind": e.match_kind,
        "contributes": e.contributes.value,
        "note": e.note,
        "provenance": _provenance_out(e.provenance),
    }


def recipe_safety_out(safety):
    """A `food_reference.RecipeSafety` as camelCase JSON.

    `screenedLines` is not decoration: every state below is bounded by it, and a
    verdict list rendered without it is a claim about a recipe rather than a
    report on the lines that were read. Same for `rulesetVersions` — the states
    are only reproducible against the rules that produced them.
    """
    a, d = safety.allergens, safety.diets
    return {
        "status": "screened",
        "screenedLines": len(safety.lines),
        # False means at least one line could not be fully read, so every
        # `unknown` below has a reason in `coverage`. The UI leads with this.
        "fullyScreened": a.fully_screened,
        "rulesetVersions": {
            "allergens": a.ruleset_version,
            "dietary": d.ruleset_version,
        },
        "allergens": [
            {
                "key": v.allergen,
                "label": v.label,
                "state": v.state.value,
                "stateText": str(v.state),
                "needsReview": v.needs_review,
                "evidence": [_evidence_out(e) for e in v.evidence],
            }
            for v in a.verdicts
        ],
        "diets": [
            {
                "key": v.diet,
                "label": v.label,
                "state": v.state.value,
                "stateText": str(v.state),
                # True only when nothing conflicted AND the diet is
                # machine-checkable from ingredient text. An advisory diet
                # (keto, paleo, whole30, gluten-free cross-contact) is never
                # certified, and `advisory` says why in the engine's own words.
                "certified": v.certified,
                "advisory": v.advisory,
                "needsReview": v.needs_review,
                "evidence": [_evidence_out(e) for e in v.evidence],
            }
            for v in d.verdicts
        ],
        # What the engine understood, line by line. The same `unscreened`
        # reasons appear inside each allergen's evidence (once per allergen,
        # with that allergen's rule id); this is the per-line view, which is how
        # a cook finds the ingredient to go and read a label on.
        "coverage": [
            {
                "ingredient": c.ingredient,
                "recognised": list(c.recognised),
                "unscreened": list(c.unscreened),
            }
            for c in a.coverage
        ],
    }


def recipe_media_out(v):
    """One item in a recipe's media slot — a photo or a how-to video.

    `fileUrl` is set only for an uploaded file the server is willing to serve
    inline; `embedUrl` only for a video provider on the embed allowlist.
    Anything else is a plain external link the UI opens in a new tab. The
    server decides what may be framed — the UI never builds an embed URL from
    a raw address.

    `kind` is what the UI switches the rendering element on, so it is always
    present and always one of the known values (the model's server default
    makes pre-media rows videos).

    `streamUrl` is the former name of `fileUrl`, and keeps its former VALUE
    too — the `/videos/<id>/stream` path, which is still routed. Pointing the
    old key at the new path would have broken a client that string-matched the
    URL it was given rather than simply following it, which is the whole class
    of breakage the alias exists to prevent.

    `display_kind`, not `normalize_kind`: this runs for every media row of
    every recipe in the library listing, so a validator that raises here turns
    one odd database value into a 500 on the whole page instead of one
    mis-rendered tile.
    """
    from ..services.videos import display_kind, embed_url
    file_url = (f"/api/v1/recipes/{v.recipe_id}/media/{v.id}/file"
                if v.filename else None)
    stream_url = (f"/api/v1/recipes/{v.recipe_id}/videos/{v.id}/stream"
                  if v.filename else None)
    return {
        "id": v.id,
        "kind": display_kind(v.kind),
        "title": v.title,
        "url": v.url or None,
        "embedUrl": embed_url(v.url) if v.url else None,
        "fileUrl": file_url,
        "streamUrl": stream_url,
        "position": v.position,
        "createdAt": iso(v.created_at),
    }


# Former name, kept because it is imported by name elsewhere.
recipe_video_out = recipe_media_out


def recipe_out(r):
    data = recipe_summary(r)
    data.update(
        {
            "sourceUrl": safe_url(r.source_url),
            "prepMinutes": r.prep_minutes,
            "cookMinutes": r.cook_minutes,
            "notes": r.notes,
            "nutrition": json.loads(r.nutrition) if r.nutrition else None,
            "effectiveNutrition": _effective_nutrition(r),
            # Whether that number was multiplied from reference data or guessed
            # by a model, and (for arithmetic) how much of the recipe's mass it
            # actually covers. Absent means "we do not know", never "fine".
            "nutritionSource": _effective_nutrition_source(r),
            "nutritionCompleteness": _effective_nutrition_completeness(r),
            "shareToken": r.share_token,
            "ingredients": [ingredient_out(i) for i in r.ingredients],
            "steps": [step_out(s) for s in r.steps],
            # Phase headings and the stated-time summary, derived from the step
            # text on read. Derived rather than stored on purpose: it is a view
            # of the author's words, so editing a step can never leave a stale
            # heading or a stale clock behind, and no migration is involved.
            # `steps` above stays the authoritative, unannotated list — every
            # existing consumer is untouched.
            "stepStructure": step_structure.structure_steps(
                [{"title": s.title, "text": s.text} for s in r.steps]
            ),
        }
    )
    return data


def recipe_public_out(r):
    """Read-only view for a public share link — display fields only, no ids,
    no group, no owner. Deliberately minimal to avoid leaking internals."""
    return {
        "name": r.name,
        "description": r.description,
        "servings": r.servings,
        "recipeYield": r.recipe_yield,
        "prepMinutes": r.prep_minutes,
        "cookMinutes": r.cook_minutes,
        "totalMinutes": r.total_minutes,
        "sourceUrl": safe_url(r.source_url),
        "hasImage": bool(r.image),
        "nutrition": json.loads(r.nutrition) if r.nutrition else None,
        "tags": [t.name for t in r.tags],
        "categories": [c.name for c in r.categories],
        "ingredients": [i.display for i in r.ingredients],
        "steps": [s.text for s in r.steps],
    }
