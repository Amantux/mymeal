"""Serialization helpers producing camelCase JSON for the SPA + API clients."""
import json
from datetime import datetime, date


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
        "aliases": [a.strip() for a in (f.aliases or "").split(",") if a.strip()],
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
    return {
        "id": ing.id,
        "display": ing.display,
        "quantity": ing.quantity,
        "note": ing.note,
        "section": ing.section,
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

    return {
        "id": r.id,
        "name": r.name,
        "slug": r.slug,
        "description": r.description,
        "cookTemperatureC": r.cook_temperature_c,
        "cookTemperature": format_temperature(r.cook_temperature_c),
        "image": f"/api/v1/recipes/{r.id}/image" if r.image else None,
        "videos": [recipe_video_out(v) for v in r.videos],
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


def recipe_video_out(v):
    """A recipe's how-to video.

    `streamUrl` is set only for an uploaded file the server is willing to play
    inline; `embedUrl` only for a provider on the embed allowlist. Anything else
    is a plain external link the UI opens in a new tab. The server decides what
    may be framed — the UI never builds an embed URL from a raw address.
    """
    from ..services.videos import embed_url
    return {
        "id": v.id,
        "title": v.title,
        "url": v.url or None,
        "embedUrl": embed_url(v.url) if v.url else None,
        "streamUrl": (f"/api/v1/recipes/{v.recipe_id}/videos/{v.id}/stream"
                      if v.filename else None),
        "position": v.position,
        "createdAt": iso(v.created_at),
    }


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
            "shareToken": r.share_token,
            "ingredients": [ingredient_out(i) for i in r.ingredients],
            "steps": [step_out(s) for s in r.steps],
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
