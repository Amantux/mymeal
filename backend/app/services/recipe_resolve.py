"""Rank recipe matches and decide whether we are confident enough to act.

Resolving "chicken" to a recipe is a guess. Acting on a wrong guess is how the
wrong recipe gets edited, planned, or deleted — so resolution here returns a
CONFIDENCE, not just a best hit:

* **high** — act on the single recipe with no further questions.
* **low**  — hand back the top few candidates so the caller can ask which was
  meant. Callers must not act on a low-confidence result.

Ranking looks at the tag list and description, not just the name, so "quick" or
"curry" finds recipes that only say so in their tags — and ``matched_on`` records
WHY something matched so a caller can explain the choice to a user.

This module is the single implementation of that policy. The MCP server and the
Home Assistant integration are separate processes, so they consume it through
the ``/recipes/resolve`` endpoint rather than importing it — the logic still
lives in exactly one place.
"""
from __future__ import annotations

# Score tiers. The gaps are deliberately wide so a stronger KIND of match always
# beats a weaker one regardless of how many weak matches pile up: an exact name
# outranks any prefix, a prefix outranks a bare substring, and a name match of
# any kind outranks tag/description evidence.
SCORE_EXACT_NAME = 100
SCORE_NAME_PREFIX = 70
SCORE_NAME_CONTAINS = 50
SCORE_EXACT_TAG = 40
SCORE_TAG_CONTAINS = 25
SCORE_DESCRIPTION = 10

# "High confidence" means one of:
#   * a unique exact name match (handled below), or
#   * the top hit is at least a name-prefix match AND clears the runner-up by a
#     full tier.
# STRONG_SCORE is set to the prefix tier because a substring-only top hit
# ("chicken" inside "Butter Chicken") is genuinely weak evidence of intent.
# DOMINANCE_MARGIN of one tier means "Pasta Bake" (prefix, 70) wins over recipes
# merely tagged pasta (25), while "Chicken Soup" (70) vs "Butter Chicken" (50)
# stays ambiguous — which it is.
STRONG_SCORE = SCORE_NAME_PREFIX
DOMINANCE_MARGIN = 30

MAX_CANDIDATES = 5
DESCRIPTION_SNIPPET = 140


def score_match(query: str, name: str, tags=None, description: str = ""):
    """``(score, matched_on)`` for one recipe against a query.

    ``matched_on`` is ``"name"``, ``"tag"``, ``"description"``, or None when
    nothing matched (or the query is empty, where everything ties at 0).
    """
    q = (query or "").strip().casefold()
    if not q:
        return 0, None

    name_cf = (name or "").strip().casefold()
    if name_cf == q:
        return SCORE_EXACT_NAME, "name"
    if name_cf.startswith(q):
        return SCORE_NAME_PREFIX, "name"
    if q in name_cf:
        return SCORE_NAME_CONTAINS, "name"

    tag_names = [str(t or "").strip().casefold() for t in (tags or [])]
    if any(t == q for t in tag_names):
        return SCORE_EXACT_TAG, "tag"
    if any(q in t for t in tag_names):
        return SCORE_TAG_CONTAINS, "tag"

    if q in (description or "").casefold():
        return SCORE_DESCRIPTION, "description"
    return 0, None


def rank(rows, query: str, *, get=None):
    """Sort ``rows`` best-match first, returning ``[(row, score, matched_on)]``.

    ``get(row) -> (name, tags, description)`` adapts whatever shape the caller
    holds (ORM object or serialized dict); the default reads dict keys. Ties fall
    back to name order so results are stable rather than arbitrary.
    """
    get = get or _from_dict
    scored = []
    for row in rows:
        name, tags, description = get(row)
        score, matched_on = score_match(query, name, tags, description)
        scored.append((row, score, matched_on, (name or "").casefold()))
    scored.sort(key=lambda item: (-item[1], item[3]))
    return [(row, score, matched_on) for row, score, matched_on, _ in scored]


def _from_dict(row):
    tags = row.get("tags") or []
    # Tags may be serialized objects ({"name": ...}) or plain strings.
    names = [t.get("name") if isinstance(t, dict) else t for t in tags]
    return row.get("name"), names, row.get("description") or ""


def is_confident(ranked) -> bool:
    """Whether the top of a ranked list is a safe single answer.

    True when there is only one match, when exactly one match is an exact name
    hit, or when the leader is a strong match that clears the runner-up by a
    tier. Everything else is a coin-flip and must be disambiguated.
    """
    if not ranked:
        return False
    if len(ranked) == 1:
        return True
    exact = [item for item in ranked if item[1] == SCORE_EXACT_NAME]
    if len(exact) == 1:
        return True
    if exact:
        return False  # several recipes share a name — never guess between them
    top, second = ranked[0][1], ranked[1][1]
    return top >= STRONG_SCORE and (top - second) >= DOMINANCE_MARGIN


def candidate_out(row, matched_on, score=None) -> dict:
    """The shape a caller shows a user when asking which recipe was meant."""
    name, tags, description = _from_dict(row)
    text = (description or "").strip()
    if len(text) > DESCRIPTION_SNIPPET:
        text = text[:DESCRIPTION_SNIPPET].rstrip() + "…"
    out = {
        "id": row.get("id"),
        "name": name,
        "tags": [t for t in tags if t],
        "description": text,
        "matchedOn": matched_on,
    }
    if score is not None:
        out["score"] = score
    return out


def lookup(gid: str, query: str, limit: int = MAX_CANDIDATES) -> dict:
    """Resolve a free-text recipe reference for one group, against the database.

    The DB-backed companion to :func:`decide`, so every caller that turns "the
    chicken one" into a recipe — the REST resolve endpoint, the chat agent, and
    through them the MCP server and the HA integration — runs the SAME query and
    the SAME confidence rule. A caller that hand-rolls its own ``ilike(...).first()``
    silently reintroduces the guessing this module exists to prevent.

    Returns :func:`decide`'s shape, except a ``"high"`` result carries the ORM
    ``Recipe`` under ``"match"``.
    """
    # Imported here so the scoring half of this module stays pure and importable
    # without an app context (it is unit-tested that way).
    from sqlalchemy.orm import selectinload

    from ..extensions import db
    from ..models import Recipe, Tag

    q = (query or "").strip()
    if not q:
        return {"confidence": "none", "candidates": []}

    # An id or slug is an unambiguous handle the caller already holds (typically
    # from a previous search) — never re-fuzz it.
    direct = db.session.get(Recipe, q)
    if not direct or direct.group_id != gid:
        direct = db.session.query(Recipe).filter_by(group_id=gid, slug=q).first()
    if direct:
        return {"confidence": "high", "match": direct, "matchedOn": "id"}

    like = f"%{q}%"
    rows = (
        db.session.query(Recipe)
        .filter_by(group_id=gid)
        .filter(
            db.or_(
                Recipe.name.ilike(like),
                Recipe.description.ilike(like),
                Recipe.tags.any(Tag.name.ilike(like)),
            )
        )
        .options(selectinload(Recipe.tags))
        .all()
    )
    by_id = {r.id: r for r in rows}
    decision = decide(
        [{"id": r.id, "name": r.name, "tags": [t.name for t in r.tags],
          "description": r.description or ""} for r in rows],
        q,
        limit=limit,
    )
    if decision["confidence"] == "high":
        decision["match"] = by_id[decision["match"]["id"]]
    return decision


def decide(rows, query: str, limit: int = MAX_CANDIDATES) -> dict:
    """Rank ``rows`` and return a confidence decision.

    ``{"confidence": "high", "match": row, "matchedOn": ...}`` when it is safe to
    act, ``{"confidence": "low", "candidates": [...]}`` when the caller must ask,
    or ``{"confidence": "none", "candidates": []}`` when nothing matched.
    """
    ranked = [item for item in rank(rows, query) if item[1] > 0 or not query]
    if not ranked:
        return {"confidence": "none", "candidates": []}
    if is_confident(ranked):
        row, score, matched_on = ranked[0]
        return {"confidence": "high", "match": row, "matchedOn": matched_on,
                "score": score}
    return {
        "confidence": "low",
        "candidates": [
            candidate_out(row, matched_on, score)
            for row, score, matched_on in ranked[:limit]
        ],
    }
