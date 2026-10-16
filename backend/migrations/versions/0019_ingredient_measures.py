"""Fold alias-spelled Unit rows into their canonical sibling

`canonical_unit` output is STORED, not only derived: `_find_or_create_unit`
mints a `Unit` row from it on every recipe save, and shopping consolidation
groups on that row's `abbreviation or name`. So collapsing the unit tables to
one canonical key per unit fixes every recipe saved from now on and does
nothing for the ones already in the database — which still hold
`Unit(name="gram")` beside `Unit(name="g")`, and still buy the parmesan twice.
That is the exact symptom the change claims to fix, so the data has to move too.

A DATA migration, not a schema one: no column changes, so `create_all()` and a
migrated database already agree. For every unit whose name is an alias spelling
("gram", "ounce", "liter", "cc", "teaspoon", "fluid ounce", …):

  * if the group already has the canonical row, repoint its ingredients at that
    row and delete the alias;
  * otherwise rename the alias row in place.

Group-scoped throughout — units belong to a household and must never be folded
across one. Idempotent: a second run finds every name already canonical and
does nothing, which matters because migrations WILL be retried after a failure.

Deliberately NOT included: `unit_conversions.unit`, which can also hold a legacy
spelling. That table is a learned-weight CACHE keyed by (group, unit, food); a
stale key costs one re-lookup, never a wrong answer, and folding it would have
to resolve collisions against its unique constraint with two different
confidence values. Not worth the risk for a cache miss.

Revision ID: 0019_ingredient_measures
Revises: 0018_ingredient_free_text
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0019_ingredient_measures"
down_revision = "0018_ingredient_free_text"
branch_labels = None
depends_on = None


def _key(name: str, abbreviation: str) -> str:
    """The string shopping consolidation actually groups on."""
    return (abbreviation or name or "").strip()


def upgrade() -> None:
    # Imported here, not at module scope: a migration must not drag the app's
    # service layer into every alembic invocation.
    from app.services.units import canonical_unit

    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "units" not in insp.get_table_names():
        return

    rows = bind.execute(sa.text(
        "SELECT id, group_id, name, abbreviation FROM units ORDER BY id"
    )).fetchall()

    # group_id -> {canonical key (lowercased): unit id}. Seeded with the rows
    # that are ALREADY canonical, then updated as aliases are renamed, so two
    # aliases folding to the same canonical in a group with no canonical row
    # merge into one another instead of both being renamed to a duplicate.
    canonical_rows: dict[tuple[str, str], str] = {}
    pending = []
    for unit_id, group_id, name, abbreviation in rows:
        key = _key(name, abbreviation)
        target = canonical_unit(key)
        if not target:
            continue            # a household's own unit ("batch") — not ours
        if key == target:
            canonical_rows.setdefault((group_id, target), unit_id)
        else:
            pending.append((unit_id, group_id, name, abbreviation, target))

    for unit_id, group_id, name, abbreviation, target in pending:
        existing = canonical_rows.get((group_id, target))
        if existing and existing != unit_id:
            bind.execute(
                sa.text("UPDATE recipe_ingredients SET unit_id = :keep "
                        "WHERE unit_id = :drop"),
                {"keep": existing, "drop": unit_id},
            )
            bind.execute(sa.text("DELETE FROM units WHERE id = :id"),
                         {"id": unit_id})
            continue
        # No canonical row to fold into: this row becomes it. `abbreviation` is
        # rewritten only when it was the alias being matched on, so a household
        # that set one deliberately keeps a meaningful value.
        bind.execute(
            sa.text("UPDATE units SET name = :name, abbreviation = :abbr "
                    "WHERE id = :id"),
            {"name": target, "abbr": target if abbreviation else abbreviation,
             "id": unit_id},
        )
        canonical_rows[(group_id, target)] = unit_id


def downgrade() -> None:
    """Deliberately a no-op, and not a silently wrong one.

    Nothing schema-wise changed, so there is nothing to reverse. The merge
    itself cannot be: which of a household's recipes originally said "gram"
    rather than "g" is not recorded anywhere, and re-splitting them would
    restore the duplicate-shopping-row defect rather than a previous state.
    Leaving the canonical rows in place is correct under 0018's schema too.
    """
