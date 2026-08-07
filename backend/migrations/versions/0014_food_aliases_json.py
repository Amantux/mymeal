"""foods.aliases: comma-separated String(512) -> JSON list

The delimited column caused two proven defects in the merge endpoint (a food
named "salt, kosher" forged two aliases via the delimiter; append-only merges
overflowed 512 chars, which Postgres rejects and which then bricks even a plain
PUT on the row) and spawned four divergent hand-rolled parsers. JSON is the
shape the sibling column ``allergens`` already uses, for the reason 0013's own
docstring gives, and matches Edibl's ``FoodConcept.aliases``.

Order inside ``upgrade`` is load-bearing:

1. **Data first, while the column is still text.** SQLite's batch alter is a
   table rebuild that copies values as-is, so a type change alone would leave
   literal ``a,b`` text sitting in a JSON column; and on Postgres
   ``aliases::json`` fails on ``''`` and on any non-JSON text. The backfill
   rewrites every row to valid JSON array text, making the cast safe.
2. Then the type change, with the same PRAGMA dance as HomeHoard's
   0014_money_numeric: the rebuild does DROP TABLE on ``foods``, two foreign
   keys reference ``foods.id`` (recipe_ingredients, shopping_list_items) and
   this app turns FK enforcement ON (extensions.py) — so on any database with
   data the rebuild raises "FOREIGN KEY constraint failed" unless enforcement
   is suspended. 0013's "no PRAGMA dance needed" comment does NOT transfer:
   that was about columns nobody references; the hazard here is the table.
   The re-enable lives in a ``finally`` because migrations run in-process and
   the connection returns to the app's pool.

Collision policy during backfill, deterministic by construction: foods are
processed per group ordered by ``(created_at, id)``; an alias whose folded key
is already claimed by an earlier food's name or alias is DROPPED from the
later food, not moved. The earlier row is the one existing recipe rows more
likely point at, and dropping degrades to "not consolidated", never to "wrong
ingredient" — the same failure-direction rule as services/food_resolve. Drops
are logged.

Idempotent by data predicate: rows already holding a valid JSON array are left
untouched, so a retry after a partial failure cannot double-convert
(``"a,b"`` -> ``["a","b"]`` -> ``["[\"a\"", ...]``). A leftover
``_alembic_tmp_foods`` from a half-finished rebuild is cleaned up first.

The downgrade is real: it reads and converts every row BEFORE the type change
and writes the CSV back AFTER it (Postgres both parses json on read and
validates it on write, so in-place conversion is impossible in either order on
that side alone). Lossy only for an alias containing a comma — which the old
column could never store in the first place.

Revision ID: 0014_food_aliases_json
Revises: 0013_food_qualifier
Create Date: 2026-08-07
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0014_food_aliases_json"
down_revision = "0013_food_qualifier"
branch_labels = None
depends_on = None


def _column_type(table, column):
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return None
    for c in insp.get_columns(table):
        if c["name"] == column:
            return str(c["type"]).upper()
    return None


def _as_list(raw):
    """A stored value as a list, or None when it is already a JSON array
    (the already-converted / idempotency case)."""
    if isinstance(raw, list):
        return None  # Postgres returns parsed JSON: already converted
    text = (raw or "").strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return None  # already converted — leave alone
        except ValueError:
            pass  # malformed bracket text: treat as CSV below
    return [part.strip() for part in text.split(",") if part.strip()]


def _backfill_to_json(bind):
    # The app's fold/normalize IS the alias equivalence; re-implementing it
    # here would drift (the 0011 precedent: import the app's parser).
    from app.services.food_resolve import fold, normalize_text

    def key(term):
        return fold(normalize_text(term or ""))

    log = sa.text("SELECT id, group_id, name, aliases FROM foods "
                  "ORDER BY group_id, created_at, id")
    rows = bind.execute(log).fetchall()

    # Pass 1 — every food's NAME claims its key first. A name always outranks
    # an alias: an alias colliding with another food's name is the exact
    # mis-resolution this migration exists to prevent.
    claimed = {}
    for fid, gid, name, _aliases in rows:
        k = key(name)
        if k:
            claimed.setdefault((gid, k), fid)

    update = sa.text("UPDATE foods SET aliases = :value WHERE id = :id")
    for fid, gid, _name, aliases in rows:
        terms = _as_list(aliases)
        if terms is None:
            continue  # already a JSON array
        kept, seen = [], set()
        for term in terms:
            k = key(term)
            if not k or k in seen:
                continue
            owner = claimed.get((gid, k))
            if owner is not None and owner != fid:
                # Claimed by an earlier food (or a name). Drop, don't move.
                print(f"0014: dropping alias {term!r} from food {fid} "
                      f"(key {k!r} already claimed by {owner})")
                continue
            claimed[(gid, k)] = fid
            seen.add(k)
            kept.append(term)
        bind.execute(update, {"value": json.dumps(kept), "id": fid})


def _collect_csv(bind):
    """Every row's aliases as the CSV string the old column stored, computed
    BEFORE the type change and written back AFTER it.

    Both halves are Postgres lessons the SQLite tests cannot teach (found by
    running against a real Postgres):
      * reading a json column yields a PARSED list (psycopg decodes it), so
        the value may already be a list, not text;
      * writing CSV text into a still-json column is rejected outright —
        Postgres validates JSON on write, SQLite happily stores anything. So
        the conversion cannot happen in place before the alter, and after the
        alter the JSON text needs replacing anyway. Read-convert-hold-write.
    """
    rows = bind.execute(sa.text("SELECT id, aliases FROM foods")).fetchall()
    out = {}
    for fid, aliases in rows:
        if isinstance(aliases, list):
            terms = aliases
        else:
            text = (aliases or "").strip()
            if not text.startswith("["):
                continue  # already CSV (idempotent retry)
            try:
                terms = json.loads(text)
            except ValueError:
                continue
        kept, total = [], 0
        for term in terms:
            term = str(term).replace(",", " ").strip()
            if not term:
                continue
            # Whole terms only, into the 512 budget the old column enforces.
            if total + len(term) + (1 if kept else 0) > 500:
                break
            total += len(term) + (1 if kept else 0)
            kept.append(term)
        out[fid] = ",".join(kept)
    return out


def _alter(to_json: bool) -> None:
    current = _column_type("foods", "aliases") or ""
    want_json = "JSON" in current
    if to_json == want_json:
        return  # already in the target shape (idempotent)

    bind = op.get_bind()
    sqlite = bind.dialect.name == "sqlite"
    if sqlite:
        # ⚠️ PRAGMA foreign_keys is a NO-OP inside a transaction, and the
        # backfill's UPDATEs have already opened one implicitly — so issuing
        # the PRAGMA directly here silently does nothing and the rebuild dies
        # on "FOREIGN KEY constraint failed" anyway. (HomeHoard's 0014 never
        # hit this because it has no data pass before its PRAGMA.) The
        # autocommit_block commits the open transaction and runs these
        # statements outside one; the pragma is CONNECTION state, so it stays
        # in effect for the rebuild that follows.
        with op.get_context().autocommit_block():
            op.execute('DROP TABLE IF EXISTS "_alembic_tmp_foods"')
            op.execute("PRAGMA foreign_keys=OFF")
    else:
        # Postgres refuses ALTER TYPE when the existing column DEFAULT cannot
        # be auto-cast to the new type ("default for column ... cannot be cast
        # automatically") — which is exactly the round-trip case, since each
        # direction leaves a default of the other type. The USING clause only
        # covers values, not the default. Drop it; the alter sets the new one.
        op.execute("ALTER TABLE foods ALTER COLUMN aliases DROP DEFAULT")
    try:
        with op.batch_alter_table("foods") as batch:
            batch.alter_column(
                "aliases",
                existing_type=sa.String(length=512) if to_json else sa.JSON(),
                type_=sa.JSON() if to_json else sa.String(length=512),
                existing_nullable=False,
                nullable=False,
                # '[]' so create_all() and a migrated DB describe the same
                # table — 0013's stated rule, which `allergens` broke (0015).
                server_default=sa.text("'[]'") if to_json else "",
                # Downgrade values are overwritten straight after the alter,
                # so the cast only needs to not ERROR; left() removes the
                # overlength risk for a long JSON text.
                postgresql_using="aliases::json" if to_json
                else "left(aliases::text, 512)",
            )
    finally:
        if sqlite:
            # Same rule on the way back: outside a transaction or it no-ops,
            # and this connection returns to the app's pool with enforcement
            # expected ON.
            with op.get_context().autocommit_block():
                op.execute("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    # The backfill runs UNCONDITIONALLY, not gated on the column type: it is
    # idempotent by data predicate (rows already holding a JSON array are
    # skipped), and gating it on type misses a real hybrid — 0001_baseline is
    # metadata-driven, so a database whose baseline ran under the NEW model
    # already has a JSON-typed column, which SQLite will happily let CSV text
    # sit in. Type answers "does the ALTER need to run", not "is the data
    # converted"; those are different questions.
    _backfill_to_json(op.get_bind())
    _alter(to_json=True)


def downgrade() -> None:
    bind = op.get_bind()
    csv_by_id = {}
    if "JSON" in (_column_type("foods", "aliases") or ""):
        csv_by_id = _collect_csv(bind)
    _alter(to_json=False)
    update = sa.text("UPDATE foods SET aliases = :value WHERE id = :id")
    for fid, value in csv_by_id.items():
        bind.execute(update, {"value": value, "id": fid})
