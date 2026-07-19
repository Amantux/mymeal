"""Serialization helpers producing camelCase JSON for the SPA + API clients."""
import json
from datetime import datetime, date


def iso(dt):
    if dt is None:
        return None
    if isinstance(dt, (datetime, date)):
        return dt.isoformat()
    return dt


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
    }


def step_out(s):
    return {"id": s.id, "position": s.position, "title": s.title, "text": s.text}


def recipe_summary(r):
    return {
        "id": r.id,
        "name": r.name,
        "slug": r.slug,
        "description": r.description,
        "image": f"/api/v1/recipes/{r.id}/image" if r.image else None,
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


def pantry_item_out(p):
    return {
        "id": p.id,
        "label": p.label,
        "quantity": p.quantity,
        "unit": p.unit,
        "location": p.location,
        "expiresAt": iso(p.expires_at),
        "foodId": p.food_id,
        "food": food_out(p.food) if p.food else None,
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


def recipe_out(r):
    data = recipe_summary(r)
    data.update(
        {
            "sourceUrl": r.source_url,
            "prepMinutes": r.prep_minutes,
            "cookMinutes": r.cook_minutes,
            "notes": r.notes,
            "nutrition": json.loads(r.nutrition) if r.nutrition else None,
            "ingredients": [ingredient_out(i) for i in r.ingredients],
            "steps": [step_out(s) for s in r.steps],
        }
    )
    return data
