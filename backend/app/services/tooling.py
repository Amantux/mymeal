"""AI tooling jobs (ported from HomeHoard): auto-tag recipes and propose clusters.

Confidence-gated: a high-confidence tag that already exists is applied automatically;
everything else (new tag, low confidence, any cluster grouping) lands in the review
queue (``AiSuggestion``, status ``pending``). Review decisions (accepted/rejected)
feed back as few-shot in-context-learning examples to later runs.
"""
from __future__ import annotations

import json
import logging

from flask import current_app

from ..extensions import db
from ..models import AiSuggestion, Recipe, Tag, utcnow
from ..utils import unique_slug

_LOGGER = logging.getLogger("mymeal.tooling")
_CATEGORIZE_MAX = 150   # recipes per run
_CLUSTER_ITEMS_MAX = 300


def _threshold() -> float:
    return float(current_app.config.get("AI_CONFIDENCE_THRESHOLD", 0.8))


def _confidence(value) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, c))


def existing_tags(gid) -> list[str]:
    return [t.name for t in db.session.query(Tag)
            .filter_by(group_id=gid).order_by(Tag.name.asc()).all()]


def _find_tag(gid, name):
    if not name:
        return None
    return (db.session.query(Tag)
            .filter(Tag.group_id == gid, db.func.lower(Tag.name) == name.lower())
            .first())


def _get_or_create_tag(gid, name):
    tag = _find_tag(gid, name)
    if tag is None:
        tag = Tag(name=name[:255], slug=unique_slug(Tag, gid, name), group_id=gid)
        db.session.add(tag)
        db.session.flush()
    return tag


def _example_text(sugg) -> str:
    return sugg.recipe.name if sugg.recipe else sugg.label


def icl_examples(gid, kind, limit=8) -> str:
    rows = (db.session.query(AiSuggestion)
            .filter(AiSuggestion.group_id == gid, AiSuggestion.kind == kind,
                    AiSuggestion.status.in_(("accepted", "rejected")))
            .order_by(AiSuggestion.resolved_at.desc()).limit(limit * 3).all())
    pos = [r for r in rows if r.status == "accepted"][:limit]
    neg = [r for r in rows if r.status == "rejected"][:limit]
    parts = []
    if pos:
        parts.append("Tags the user ACCEPTED (do more like these): "
                     + "; ".join(f"'{_example_text(r)}' → {r.label}" for r in pos))
    if neg:
        parts.append("Tags the user REJECTED (avoid these): "
                     + "; ".join(f"'{_example_text(r)}' → {r.label}" for r in neg))
    return "\n".join(parts)


def _job_provider(gid):
    from .ai.base import ProviderError
    from .ai.registry import provider_for_group
    from .jobs import JobError
    try:
        return provider_for_group(gid)
    except ProviderError as exc:
        raise JobError(str(exc)) from exc


def _recipe_desc(recipe) -> str:
    ings = ", ".join(i.display for i in recipe.ingredients[:8]) if recipe.ingredients else ""
    return f"{recipe.name}. {recipe.description or ''} {ings}".strip()[:400]


# --- categorize (recipe tags) ---------------------------------------------
def run_categorize(job) -> dict:
    from .jobs import bump
    gid = job.group_id
    opts = json.loads(job.params) if job.params else {}
    note = str(opts.get("note") or "")
    provider = _job_provider(gid)
    tags = existing_tags(gid)
    examples = icl_examples(gid, "categorize")
    threshold = _threshold()

    # Skip recipes already awaiting review, so a re-run doesn't queue duplicates.
    pending = (db.session.query(AiSuggestion.recipe_id)
               .filter(AiSuggestion.group_id == gid, AiSuggestion.kind == "categorize",
                       AiSuggestion.status == "pending", AiSuggestion.recipe_id.isnot(None)))
    recipes = (db.session.query(Recipe)
               .filter(Recipe.group_id == gid, ~Recipe.tags.any())
               .filter(~Recipe.id.in_(pending))
               .order_by(Recipe.created_at.asc()).limit(_CATEGORIZE_MAX).all())
    bump(job, done=0, total=len(recipes))
    applied = queued = 0
    for i, recipe in enumerate(recipes, 1):
        prompt = (
            "Assign the single best tag to this recipe. Prefer an existing tag if one "
            "fits; only propose a NEW tag if none do.\n"
            f"Existing tags: {', '.join(tags) or '(none yet)'}\n"
            + (f"{examples}\n" if examples else "")
            + (f"User guidance: {note}\n" if note else "")
            + f"Recipe: {_recipe_desc(recipe)}\n"
            'Respond ONLY as JSON: {"label": "<name>", "confidence": 0.0-1.0, "rationale": "<short>"}.'
        )
        try:
            out = provider.complete_json(prompt)
        except Exception as exc:  # noqa: BLE001 - best-effort; skip this recipe
            _LOGGER.info("tag failed for %s: %s", recipe.id, exc)
            out = {}
        name = (out.get("label") or "").strip()[:255]
        if name:
            conf = _confidence(out.get("confidence"))
            existing = _find_tag(gid, name)
            common = dict(kind="categorize", label=name, confidence=conf,
                          rationale=(out.get("rationale") or "")[:500],
                          recipe_id=recipe.id, group_id=gid)
            if conf >= threshold and existing is not None:
                if existing not in recipe.tags:
                    recipe.tags.append(existing)
                db.session.add(AiSuggestion(status="accepted", resolved_at=utcnow(), **common))
                applied += 1
            else:
                db.session.add(AiSuggestion(status="pending", **common))
                queued += 1
        bump(job, done=i)
    return {"applied": applied, "queued": queued, "scanned": len(recipes)}


# --- cluster (recipe collections → a tag) ---------------------------------
def run_cluster(job) -> dict:
    from .jobs import bump
    gid = job.group_id
    opts = json.loads(job.params) if job.params else {}
    note = str(opts.get("note") or "")
    provider = _job_provider(gid)
    recipes = (db.session.query(Recipe).filter(Recipe.group_id == gid)
               .order_by(Recipe.created_at.asc()).limit(_CLUSTER_ITEMS_MAX).all())
    bump(job, done=0, total=1)
    if not recipes:
        return {"proposed": 0, "scanned": 0}
    listing = "\n".join(f"{idx}: {r.name}" for idx, r in enumerate(recipes))
    prompt = (
        "Group these recipes into a few meaningful named collections (e.g. 'Weeknight "
        "dinners', 'Desserts'). Only group recipes that clearly belong together; leave "
        "unrelated ones out.\n"
        + (f"User guidance: {note}\n" if note else "")
        + f"Recipes (index: name):\n{listing}\n"
        'Respond ONLY as JSON: {"clusters": [{"name": "...", "indices": [0,3], "confidence": 0.0-1.0}]}.'
    )
    try:
        data = provider.complete_json(prompt, max_tokens=1500)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.info("cluster failed: %s", exc)
        data = {}
    valid = {r.id for r in recipes}
    proposed = 0
    for c in (data.get("clusters") or []):
        name = (c.get("name") or "").strip()[:255]
        seen, idxs = set(), []
        for n in (c.get("indices") or []):
            if isinstance(n, int) and not isinstance(n, bool) and 0 <= n < len(recipes) and n not in seen:
                seen.add(n)
                idxs.append(n)
        ids = [recipes[n].id for n in idxs if recipes[n].id in valid]
        if not name or len(ids) < 2:
            continue
        db.session.add(AiSuggestion(
            kind="cluster", status="pending", label=name,
            confidence=_confidence(c.get("confidence")),
            rationale=(c.get("rationale") or "")[:500],
            payload=json.dumps({"recipeIds": ids}), group_id=gid))
        proposed += 1
    bump(job, done=1, total=1)
    return {"proposed": proposed, "scanned": len(recipes)}


# --- apply / reject --------------------------------------------------------
def apply_suggestion(sugg) -> dict:
    """Accept a pending suggestion: attach its tag (creating it if new) to the recipe
    (categorize) or every member recipe (cluster). Idempotent; group-scoped."""
    gid = sugg.group_id
    tag = _get_or_create_tag(gid, sugg.label)
    targets = []
    if sugg.kind == "categorize" and sugg.recipe_id:
        r = db.session.get(Recipe, sugg.recipe_id)
        if r and r.group_id == gid:
            targets = [r]
    elif sugg.kind == "cluster":
        ids = (json.loads(sugg.payload) if sugg.payload else {}).get("recipeIds", [])
        targets = [r for r in (db.session.get(Recipe, i) for i in ids)
                   if r and r.group_id == gid]
    for r in targets:
        if tag not in r.tags:
            r.tags.append(tag)
    sugg.status = "accepted"
    sugg.resolved_at = utcnow()
    db.session.commit()
    return {"applied": len(targets), "label": tag.name}


def reject_suggestion(sugg) -> None:
    sugg.status = "rejected"
    sugg.resolved_at = utcnow()
    db.session.commit()
