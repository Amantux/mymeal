"""AI nutrition estimation — per-serving calories and macros from a recipe's
ingredient list. The estimate is best-effort and stored on ``Recipe.nutrition``
as JSON; the same ``sanitize`` guards manual edits so only known numeric fields
are ever persisted."""
from .base import AIProvider

# Canonical per-serving fields. Units: calories = kcal, macros = grams,
# sodium = milligrams.
FIELDS = ("calories", "protein", "carbs", "fat", "fiber", "sugar", "sodium")

_SYSTEM = (
    "You are a nutrition estimator. Given a recipe's ingredients and its serving "
    "count, estimate the nutrition PER SERVING from typical food values. Return "
    "ONLY JSON with numeric fields: calories (kcal); protein, carbs, fat, fiber, "
    "sugar (grams); sodium (milligrams). Use 0 for a field you cannot estimate. "
    "No commentary."
)


def sanitize(raw) -> dict:
    """Keep only the known fields, coerced to non-negative rounded numbers."""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for f in FIELDS:
        try:
            n = round(float(raw.get(f)), 1)
        except (TypeError, ValueError):
            continue
        if n != n or n < 0:  # NaN or negative
            continue
        out[f] = n
    return out


def estimate_nutrition(
    ingredient_lines: list[str], servings: int, provider: AIProvider
) -> dict:
    lines = "\n".join(f"- {ln}" for ln in ingredient_lines if str(ln).strip())
    prompt = (
        f"Servings: {servings or 'unknown (assume 4)'}\n"
        f"Ingredients:\n{lines}\n\n"
        'Return JSON like {"calories":520,"protein":28,"carbs":45,"fat":22,'
        '"fiber":6,"sugar":8,"sodium":640}.'
    )
    return sanitize(provider.complete_json(prompt, system=_SYSTEM, max_tokens=512))
