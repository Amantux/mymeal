#!/usr/bin/env python3
# Read the serializers file
with open("backend/app/schemas/serializers.py", "r") as f:
    content = f.read()

# Find the recipe_summary function and add cook event fields
old_return = '''    return {
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
    }'''

new_return = '''    # Cook event summary: count and last cooked timestamp
    cook_events = getattr(r, "cook_events", [])
    count = len(cook_events)
    last_cooked_at = cook_events[0].created_at.isoformat() if cook_events else None
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
        "cookCount": count,
        "lastCookedAt": last_cooked_at,
    }'''

content = content.replace(old_return, new_return)

# Write back
with open("backend/app/schemas/serializers.py", "w") as f:
    f.write(content)
print("Done")
