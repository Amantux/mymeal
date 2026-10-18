"""The safety endpoint must not fire queries per component row.

Split from test_recipe_safety.py because it needs an engine-level listener and
has nothing to say about the four states — but it guards a real regression
path: `_get_by_id_or_slug`'s `extra_opts` is the only thing making the
component walk a bounded number of queries, and dropping it is an invisible
change that surfaces only as latency on somebody's deeply nested recipe.

The assertion is a SLOPE, not a ceiling. A fixed "fewer than N queries" bound
is what the first version of this test used and it was vacuous: without the
eager-load the count rose from 11 to 16, still under any ceiling loose enough
not to be brittle. Measuring two recipe sizes and comparing the growth is the
only form of this test that fails when the guard is removed — verified by
removing it.
"""
from sqlalchemy import event

from app.extensions import db


def _make(auth_client, name, lines, components=()):
    rows = [{"display": line} for line in lines]
    rows += [{"display": f"1 batch {c['name']}", "quantity": 1,
              "refRecipeId": c["id"]} for c in components]
    res = auth_client.post("/api/v1/recipes", json={
        "name": name, "servings": 2, "ingredients": rows,
        "steps": [{"text": "Cook."}]})
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def _selects_for(auth_client, app, recipe_id):
    seen = []
    with app.app_context():
        engine = db.session.get_bind()

        def before(conn, cursor, statement, *a):
            if statement.lstrip().upper().startswith("SELECT"):
                seen.append(statement)

        event.listen(engine, "before_cursor_execute", before)
        try:
            res = auth_client.get(f"/api/v1/recipes/{recipe_id}/safety")
        finally:
            event.remove(engine, "before_cursor_execute", before)
    assert res.status_code == 200, res.get_json()
    return res.get_json(), seen


def _parent_with(auth_client, label, n):
    subs = [_make(auth_client, f"{label} sub {i}",
                  [f"{i + 1} g flour", f"{i + 1} ml milk", f"{i + 1} g peanuts"])
            for i in range(n)]
    return _make(auth_client, f"{label} parent", ["2 carrots"], components=subs)


def test_component_expansion_does_not_scale_the_query_count(auth_client, app):
    small = _parent_with(auth_client, "Small", 2)
    large = _parent_with(auth_client, "Large", 8)

    small_data, small_q = _selects_for(auth_client, app, small["id"])
    large_data, large_q = _selects_for(auth_client, app, large["id"])

    # It really did expand. The count is the parent's own row, plus each
    # component ROW (which is screened too — that is what stops an
    # unexpandable one from vanishing), plus that component's three lines.
    assert small_data["screenedLines"] == 1 + 2 * (1 + 3)
    assert large_data["screenedLines"] == 1 + 8 * (1 + 3)
    assert any(a["key"] == "peanuts" and a["state"] == "contains"
               for a in large_data["allergens"])

    growth = len(large_q) - len(small_q)
    # Six more components must not mean six (or twelve) more round trips.
    # Eager-loaded, the extra components ride along in the same IN clauses.
    assert growth <= 1, (
        f"{len(small_q)} queries for 2 components, {len(large_q)} for 8 "
        f"(+{growth}) — the component eager-load is no longer reaching the "
        "safety endpoint, so each sub-recipe's ingredients are lazy-loading."
    )
