"""Reference data: the record types, the one storage format, and the lookups.

There is exactly ONE ``Dataset`` in foodcore, and it is this one. For a while
there were two — an in-memory one built by the USDA adapter and a SQLite one
behind the public API — with two parsers of the same five upstream CSVs and two
unit vocabularies. Nothing had integrated against either yet, so they were
collapsed here rather than left to drift (see ``sources/usda.py``). The split
that remains is by RESPONSIBILITY, not by storage:

* ``units.py``        the spelling vocabulary, shared by all of the below;
* ``sources/*.py``    parse ONE provider's export into the records below;
* this module         the records, the file format, and reading it back.

Why SQLite, and why a file at all
---------------------------------
The reference data we care about (USDA portion weights, per-100 g nutrients) is
tens of megabytes of mostly-cold rows that are queried by key. Options were:

* **JSON/CSV in memory** — simple, but a full USDA portions+nutrients load is
  hundreds of MB resident in every gunicorn worker of every host app. Rejected.
* **A database server** — a second deployment for the user to install, which is
  exactly what this package was turned into a library to avoid. Rejected.
* **One SQLite file** — random access by key without loading the dataset,
  one file the host can place in its own data dir and back up with everything
  else, and ``sqlite3`` is in the **standard library**, so it costs zero
  dependencies. Chosen.

Where the file comes from, in order:

1. the path passed to ``load_dataset(path)`` — a host app that knows its own
   data dir should always use this;
2. ``$FOODCORE_DATASET`` — for a host that configures by environment;
3. the **bundled minimal fallback**, so ``import foodcore`` and a test suite
   work with no setup at all.

The bundled fallback contains only definitional facts (unit identities and the
density of water), labelled ``source="internal"``, ``method="inferred"``. It
contains **no USDA or Open Food Facts data**: shipping a handful of
remembered-looking nutrient numbers under a ``measured``/``CC0-1.0`` label
would be fabricated provenance, which is the one thing this package is built to
make impossible. ``Dataset.is_minimal`` is True for it so a host can tell the
user their reference data has not been imported yet.

The bulk dataset is NEVER committed: it is built on the host with
``python3 -m foodcore.cli build-usda`` (see ``cli.py``).

A dataset VERSION is a file, not a row
--------------------------------------
``build_dataset`` writes to a temporary path and renames, so a build either
replaces the dataset atomically or leaves it untouched; a host that wants to
keep the previous snapshot keeps the previous file. Rebuilding from the same
export yields a byte-identical file, which is what makes "re-import changes
nothing" checkable rather than asserted. Several provider snapshots can coexist
inside one file — the ``source`` table is keyed ``(provider, dataset_version)``
— and every value's provenance names the one it came from.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from .provenance import Outcome, Provenance, Resolved, Unresolved
from .registry import SourceRecord
from .units import VOLUME_ML, measure_key, tokenise

#: Set by a host app that configures through the environment. Checked only when
#: no explicit path is given — never as a default location.
DATASET_ENV_VAR = "FOODCORE_DATASET"

_BUNDLED_RESOURCE = "bundled.json"

# Bumped when the table layout below changes incompatibly. A Dataset refuses to
# open a file built for a DIFFERENT schema rather than silently mis-reading it.
# 2: portions gained size/qualifier/amount/seq_num/raw_text and foods gained a
#    token index, so one food can publish "cup, chopped" and "cup, sliced"
#    without them colliding on one primary key.
_SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE source (
    provider        TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    retrieved_at    TEXT NOT NULL,
    license         TEXT NOT NULL,
    record_count    INTEGER NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (provider, dataset_version)
);

CREATE TABLE food (
    key       TEXT PRIMARY KEY,   -- "<provider>:<source_id>", stable across builds
    name      TEXT NOT NULL,
    provider  TEXT NOT NULL,
    source_id TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT '',
    -- Search PREFILTER only, never the decision: the singularised tokens of
    -- `name`, space-padded so LIKE '% tok %' narrows on whole tokens rather
    -- than substrings. It exists so a one-word query on a 300k-row export need
    -- not read the table into memory. Correctness comes from the Python
    -- re-check in resolve_food, which is why the padding can be removed
    -- without changing any answer (only the number of rows examined).
    tokens    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX food_name ON food (name);
CREATE INDEX food_source ON food (provider, source_id);

CREATE TABLE portion (
    food_key   TEXT NOT NULL REFERENCES food (key),
    measure    TEXT NOT NULL,     -- canonical unit: "cup", "tbsp", "slice"
    -- A size word ("medium") and a preparation qualifier ("chopped") are part
    -- of WHICH measurement this is, so they are part of the key. Flattening
    -- them into `measure` is what made onion's "cup, chopped" (160 g) and
    -- "cup, sliced" (115 g) overwrite each other, turning a refusal into a
    -- silent 39% error.
    size       TEXT NOT NULL DEFAULT '',
    qualifier  TEXT NOT NULL DEFAULT '',
    amount     REAL NOT NULL DEFAULT 1.0,   -- upstream's "3 cloves = 9 g" -> 3
    grams      REAL NOT NULL,               -- for `amount` of the measure
    seq_num    INTEGER,                     -- upstream display order; tie-break
    raw_text   TEXT NOT NULL DEFAULT '',    -- the modifier as published
    method     TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_id  TEXT NOT NULL,
    PRIMARY KEY (food_key, measure, size, qualifier, source_id)
);
CREATE INDEX portion_lookup ON portion (food_key, measure);

CREATE TABLE nutrient (
    food_key   TEXT NOT NULL REFERENCES food (key),
    nutrient   TEXT NOT NULL,     -- "energy", "protein", "fat_total", …
    per_100g   REAL NOT NULL,
    unit       TEXT NOT NULL,     -- "kcal", "g", "mg"
    method     TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_id  TEXT NOT NULL,
    PRIMARY KEY (food_key, nutrient)
);
"""


class DatasetError(Exception):
    """The dataset file is missing, unreadable, or of an unusable schema.

    A *curated* message: it names the path and the schema version, never the
    underlying driver text.
    """


# --------------------------------------------------------------------------
# The records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FoodRow:
    """A food as the dataset knows it, WITHOUT its measurements.

    What ``search_foods`` returns. Deliberately not a thinned-out
    ``FoodRecord``: a record whose ``portions`` is empty says "this food
    publishes no portions", and a candidate list must not say that about every
    candidate. If you need the measurements, ask for ``food_record(key)``.
    """

    key: str
    name: str
    provider: str
    source_id: str


@dataclass(frozen=True)
class Portion:
    """One measured household portion. ``grams_per_unit`` is the useful bit."""

    food_key: str
    measure: str
    size: str | None
    qualifier: str
    amount: float
    gram_weight: float
    method: str = "measured"
    confidence: float = 0.95
    source_id: str = ""
    seq_num: int | None = None
    #: The modifier exactly as the provider published it, for display and audit.
    raw_text: str = ""

    @property
    def grams_per_unit(self) -> float:
        """Divide by `amount`: upstream's '3 cloves = 9 g' is 3 g per clove."""
        return self.gram_weight / self.amount if self.amount else 0.0


#: States that must never be collapsed into one another. Spec FR-01's regression
#: list names "1 cup rice" raw vs cooked explicitly: 185 g vs 158 g in the real
#: data, a 17% error if they merge.
STATES = ("raw", "cooked", "dry", "canned", "frozen", "dried", "toasted", "boiled")


@dataclass(frozen=True)
class FoodRecord:
    """A food and its measurements. The thing the engines actually reason over.

    Produced two ways from the same definition: by a source adapter parsing an
    export (on the operator's machine) and by ``Dataset.food_record`` reading
    the built file (in a host app's request path). One definition is the point —
    when the parse and the read had separate record types, the parse knew about
    preparation qualifiers and the read did not.
    """

    key: str
    description: str
    provider: str
    source_id: str
    data_type: str = ""
    portions: tuple[Portion, ...] = ()
    #: foodcore nutrient key -> (amount per 100 g, unit). Loaded, not
    #: interpreted — nutrition arithmetic is a sibling module's job.
    nutrients: dict[str, tuple[float, str]] = field(default_factory=dict)

    @property
    def head(self) -> tuple[str, ...]:
        """Tokens before the first comma: USDA's head noun ('Wheat flour')."""
        return tokenise(self.description.split(",")[0])

    @property
    def tokens(self) -> tuple[str, ...]:
        return tokenise(self.description)

    @property
    def states(self) -> frozenset[str]:
        return frozenset(s for s in STATES if s in self.tokens)

    def portion(self, measure: str, size: str | None = None) -> Portion | None:
        """Best portion for `measure` (+ `size` when given), or None.

        **Only UNQUALIFIED rows are eligible.** This is the refuse-rather-than-
        fabricate rule applied to portion selection, and it is not fussiness:
        onions publish "cup, chopped" 160 g and "cup, sliced" 115 g and no plain
        cup, cream publishes "cup, whipped" 120 g and "cup, fluid" 238 g, and
        cheddar publishes shredded 113 g / diced 132 g / melted 244 g. Letting a
        bare "1 cup" pick whichever row sorts first is a silent 2x error, so a
        bare "1 cup onion" is treated as genuinely under-specified instead (see
        `qualified_portions`).

        Ties break on seq_num then source_id, so the answer is stable across
        loads rather than dependent on row order.
        """
        same = [p for p in self.portions
                if p.measure == measure and p.gram_weight > 0 and not p.qualifier
                and p.size == size]
        if not same:
            return None
        return sorted(same, key=lambda p: (p.seq_num if p.seq_num is not None else 9999,
                                           p.source_id))[0]

    def sized_portions(self, measure: str) -> tuple[Portion, ...]:
        return tuple(p for p in self.portions
                     if p.measure == measure and p.size is not None)

    def qualified_portions(self, measure: str) -> tuple[Portion, ...]:
        """Rows for `measure` that carry a preparation qualifier — the ones
        `portion()` deliberately refuses to choose between."""
        return tuple(p for p in self.portions
                     if p.measure == measure and p.qualifier and p.size is None)

    def volume_portion(self) -> Portion | None:
        """Any unqualified volume portion, for deriving a density when the
        asked-for volume unit has no portion row of its own (2 litres of milk
        from the published quart)."""
        vols = [p for p in self.portions
                if p.measure in VOLUME_ML and p.size is None and p.gram_weight > 0
                and not p.qualifier]
        if not vols:
            return None
        # Biggest measured volume = smallest relative measurement error.
        return sorted(
            vols,
            key=lambda p: (-VOLUME_ML[p.measure] * p.amount, p.source_id),
        )[0]


@dataclass(frozen=True)
class FoodMatch:
    """Outcome of resolving a name. `confident` gates every write-ish use."""

    query: str
    confident: bool
    candidates: tuple[tuple[FoodRecord, int], ...]

    @property
    def food(self) -> FoodRecord | None:
        return self.candidates[0][0] if (self.confident and self.candidates) else None

    def describe(self, limit: int = 5) -> str:
        """Candidate list for an Unresolved detail — the metadata that actually
        settles it for a human: the description and the state."""
        out = []
        for rec, score in self.candidates[:limit]:
            states = "/".join(sorted(rec.states)) or "unspecified"
            out.append(f"{rec.source_id} {rec.description} [{states}] score={score}")
        return "; ".join(out)


#: Scores are by KIND of evidence with wide gaps, per the estate's resolution
#: rule: a stronger kind of match always beats a pile of weaker ones.
_SCORE_HEAD_EXACT = 100     # query IS the head noun ("butter" -> "Butter, salted")
_SCORE_HEAD_SUBSET = 70     # query inside the head ("butter" -> "Peanut butter, ...")
_SCORE_ANYWHERE = 50        # query somewhere in the full description


#: Every column ``_record`` needs. One constant so a new column cannot be added
#: to the materialiser and forgotten in one of the three queries that feed it.
_FOOD_SELECT = "SELECT key, name, provider, source_id, data_type FROM food"

#: How many candidates a LOW match returns. The caller shows a human 3-5 of
#: them; materialising every food that merely contains the query tokens would
#: make a one-word query on a 300k-row export read the whole table.
_MAX_CANDIDATES = 10


def _token_index(text: str) -> str:
    """Space-padded token string, so LIKE '% tok %' is a whole-token prefilter."""
    toks = tokenise(text)
    return f" {' '.join(toks)} " if toks else " "


def _like_escape(text: str) -> str:
    """Neutralise LIKE wildcards in a query token (used with ESCAPE '\\')."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Dataset:
    """Read-only access to one reference-data file.

    Lookups return ``Resolved | Unresolved`` like every other foodcore answer:
    a missing portion weight is a legitimate "we don't know", not a zero and
    not an exception.

    Thread-safe by construction: each thread gets its own read-only connection,
    because a host app serves requests on gunicorn threads and ``sqlite3``
    connections must not be shared across them.
    """

    def __init__(self, path: str | None, *, is_minimal: bool = False,
                 _populate: Callable[[sqlite3.Connection], None] | None = None):
        self.path = path
        self.is_minimal = is_minimal
        self._populate = _populate
        self._local = threading.local()
        # Fail at construction, not at the first lookup hours later.
        self._schema_version = int(self._meta().get("schema_version", 0))
        if self._schema_version != _SCHEMA_VERSION:
            raise DatasetError(
                f"{self._describe()} was built with dataset schema "
                f"{self._schema_version}, but this foodcore reads schema "
                f"{_SCHEMA_VERSION}. Rebuild it with "
                "`python3 -m foodcore.cli build-usda`, or upgrade foodcore."
            )
        self._sources = self._read_sources()

    # -- plumbing --------------------------------------------------------

    def _describe(self) -> str:
        return "the bundled minimal dataset" if self.path is None else f"{self.path!r}"

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        try:
            if self.path is None:
                conn = sqlite3.connect(":memory:")
                if self._populate is not None:
                    self._populate(conn)
            else:
                # mode=ro: a reference dataset is never written by the library,
                # and opening read-only means a mistake cannot corrupt the
                # host's file.
                conn = sqlite3.connect(
                    f"file:{self.path}?mode=ro", uri=True, check_same_thread=False
                )
            conn.row_factory = sqlite3.Row
        except sqlite3.Error as exc:
            # Curated: the host sees what to do, not the driver's text.
            raise DatasetError(
                f"could not open the foodcore dataset at {self._describe()}: "
                f"{type(exc).__name__}. Rebuild it with "
                "`python3 -m foodcore.cli build-usda`."
            ) from None
        self._local.conn = conn
        return conn

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        try:
            return self._connect().execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise DatasetError(
                f"the foodcore dataset at {self._describe()} is unusable: "
                f"{type(exc).__name__}. Rebuild it with "
                "`python3 -m foodcore.cli build-usda`."
            ) from None

    def _meta(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self._query("SELECT key, value FROM meta")}

    def _read_sources(self) -> tuple[SourceRecord, ...]:
        rows = self._query(
            "SELECT provider, dataset_version, retrieved_at, license, record_count, "
            "notes FROM source ORDER BY provider, dataset_version"
        )
        return tuple(SourceRecord(**dict(row)) for row in rows)

    def _source_for(self, provider: str) -> SourceRecord | None:
        matches = [s for s in self._sources if s.provider == provider]
        # Most recent snapshot wins when a provider has several.
        return max(matches, key=lambda s: s.retrieved_at) if matches else None

    # -- the surface a host app and the engines use ----------------------

    @property
    def sources(self) -> tuple[SourceRecord, ...]:
        """Every snapshot in this file. Plain data; persist it if you want to."""
        return self._sources

    @property
    def schema_version(self) -> int:
        return self._schema_version

    def food(self, key: str) -> FoodRow | None:
        rows = self._query(
            "SELECT key, name, provider, source_id FROM food WHERE key = ?", (key,)
        )
        return FoodRow(**dict(rows[0])) if rows else None

    def search_foods(self, text: str, limit: int = 10) -> list[FoodRow]:
        """Substring match on name, shortest-name first.

        Deliberately dumb: *ranking* a user's words onto a food is policy, and
        it lives in ``resolve_food`` where it can refuse. This returns
        candidates for a human to pick from; it does not decide.
        """
        needle = (text or "").strip().lower()
        if not needle:
            return []
        rows = self._query(
            "SELECT key, name, provider, source_id FROM food "
            "WHERE lower(name) LIKE ? ESCAPE '\\' ORDER BY length(name), name LIMIT ?",
            (f"%{_like_escape(needle)}%", max(1, int(limit))),
        )
        return [FoodRow(**dict(row)) for row in rows]

    # -- full records ----------------------------------------------------

    def food_record(self, key: str) -> FoodRecord | None:
        """A food WITH its portions and nutrients, or None.

        This is what ``convert`` and ``nutrition`` take. The portions come back
        with their size, qualifier and upstream amount intact, which is what
        lets the engine refuse a bare "1 cup onion" instead of guessing between
        chopped and sliced.
        """
        rows = self._query(_FOOD_SELECT + " WHERE key = ?", (key,))
        return self._record(rows[0]) if rows else None

    def _portions(self, food_key: str, measure: str | None = None) -> tuple[Portion, ...]:
        sql = (
            "SELECT food_key, measure, size, qualifier, amount, grams, seq_num, "
            "raw_text, method, confidence, source_id FROM portion WHERE food_key = ?"
        )
        params: list[Any] = [food_key]
        if measure is not None:
            sql += " AND measure = ?"
            params.append(measure)
        return tuple(
            Portion(
                food_key=p["food_key"],
                measure=p["measure"],
                size=p["size"] or None,
                qualifier=p["qualifier"] or "",
                amount=float(p["amount"]),
                gram_weight=float(p["grams"]),
                method=p["method"],
                confidence=float(p["confidence"]),
                source_id=p["source_id"],
                seq_num=p["seq_num"],
                raw_text=p["raw_text"] or "",
            )
            for p in self._query(
                sql + " ORDER BY measure, size, qualifier, source_id", params
            )
        )

    def _record(self, row: sqlite3.Row, *, measure: str | None = None) -> FoodRecord:
        """Materialise a food. ``measure`` narrows the portions to one measure —
        used by ``portion_grams``, which needs the selection policy but not the
        food's other hundred rows."""
        key = row["key"]
        nutrients = {} if measure is not None else {
            n["nutrient"]: (float(n["per_100g"]), n["unit"])
            for n in self._query(
                "SELECT nutrient, per_100g, unit FROM nutrient WHERE food_key = ? "
                "ORDER BY nutrient",
                (key,),
            )
        }
        return FoodRecord(
            key=key,
            description=row["name"],
            provider=row["provider"],
            source_id=row["source_id"],
            data_type=row["data_type"],
            portions=self._portions(key, measure),
            nutrients=nutrients,
        )

    def resolve_food(self, query: str) -> FoodMatch:
        """Confidence-tiered name resolution. Refuses rather than guesses.

        The load-bearing rule is **unique full-token coverage**: candidates must
        contain EVERY token of the query, and if exactly one food does, that is
        the answer. It falls out of this that "rice" is ambiguous (raw rice and
        cooked rice both contain "rice", and they weigh 185 g and 158 g per cup)
        while "cooked rice" is not — which is precisely spec FR-01's rule that
        raw and cooked must not merge, rather than a rule bolted on beside it.

        SQL does the prefiltering and Python does the deciding: the token
        index narrows 300k foods to a handful, then the scoring below re-checks
        coverage exactly. Moving the policy into SQL would make it unreadable
        and untestable for the sake of a lookup that is already fast.
        """
        q = tokenise(query)
        if not q:
            return FoodMatch(query=query, confident=False, candidates=())

        direct = self._by_identifier(query.strip())
        if direct is not None:
            return FoodMatch(query=query, confident=True, candidates=((direct, 100),))

        tokens = sorted(set(q))
        where = " AND ".join("tokens LIKE ? ESCAPE '\\'" for _ in tokens)
        rows = self._query(
            f"{_FOOD_SELECT} WHERE {where}",
            [f"% {_like_escape(t)} %" for t in tokens],
        )

        # Score on the rows; materialise records only for what we return.
        scored: list[tuple[sqlite3.Row, int]] = []
        for row in rows:
            rec_tokens = tokenise(row["name"])
            if not set(q) <= set(rec_tokens):
                continue   # partial coverage is not evidence, it is a coincidence
            head = tokenise(row["name"].split(",")[0])
            if tuple(q) == tuple(head) or set(q) == set(head):
                score = _SCORE_HEAD_EXACT
            elif set(q) <= set(head):
                score = _SCORE_HEAD_SUBSET
            else:
                score = _SCORE_ANYWHERE
            scored.append((row, score))

        scored.sort(key=lambda rs: (-rs[1], len(tokenise(rs[0]["name"])), rs[0]["name"]))
        if not scored:
            return FoodMatch(query=query, confident=False, candidates=())
        if len(scored) == 1:
            confident = True
        else:
            top, second = scored[0][1], scored[1][1]
            confident = top >= _SCORE_HEAD_SUBSET and (top - second) >= 30
        candidates = tuple((self._record(row), score)
                           for row, score in scored[:_MAX_CANDIDATES])
        return FoodMatch(query=query, confident=confident, candidates=candidates)

    def _by_identifier(self, text: str) -> FoodRecord | None:
        """A food named by its dataset key or its provider's own id.

        Only when it is UNAMBIGUOUS: two providers may both number a food "1",
        and an id that means two things is not an identifier. Ambiguity falls
        through to name scoring rather than picking a row.
        """
        if not text:
            return None
        rows = self._query(f"{_FOOD_SELECT} WHERE key = ? OR source_id = ?",
                           (text, text))
        return self._record(rows[0]) if len(rows) == 1 else None

    # -- the provenance-bearing lookups ----------------------------------

    def portion_grams(self, food_key: str, measure: str, *, version: str) -> Outcome:
        """Grams for ONE household measure of a food, with provenance.

        ``version`` is the caller's rule version (``foodcore.rule_version(…)``)
        so the returned provenance records which engine asked.

        Refuses the under-specified cases rather than picking a row: a food that
        publishes only "cup, chopped" and "cup, sliced" has no plain cup, and a
        food that publishes only small/medium/large pieces has no plain piece.
        """
        wanted = measure_key(measure)
        rows = self._query(_FOOD_SELECT + " WHERE key = ?", (food_key,))
        record = self._record(rows[0], measure=wanted) if rows else None
        chosen = record.portion(wanted) if record is not None else None
        if chosen is None:
            return self._no_portion(record, food_key, measure, wanted)
        return Resolved(
            value=chosen.grams_per_unit,
            unit="g",
            provenance=self._provenance(
                record.provider, f"{record.source_id}#{chosen.source_id}",
                chosen.method, chosen.confidence, version,
            ),
        )

    def _no_portion(self, record: FoodRecord | None, food_key: str,
                    measure: str, wanted: str) -> Unresolved:
        if record is not None:
            quals = sorted({p.qualifier for p in record.qualified_portions(wanted)})
            if quals:
                return Unresolved(
                    reason="qualifier_required",
                    detail=(
                        f"{record.description} publishes {measure!r} only as "
                        f"{', '.join(quals)} — these differ materially; say which"
                    ),
                )
            sizes = sorted({p.size for p in record.sized_portions(wanted) if p.size})
            if sizes:
                return Unresolved(
                    reason="size_required",
                    detail=(
                        f"{record.description} publishes {measure!r} only by size "
                        f"({', '.join(sizes)}); say which one"
                    ),
                )
        return Unresolved(
            reason="no_portion_data",
            detail=(
                f"The reference dataset has no weight for {measure!r} of "
                f"{food_key!r}. Give a weight or a volume, or import a dataset "
                "that covers this food."
                if not self.is_minimal else
                f"No weight for {measure!r} of {food_key!r}: only the bundled "
                "minimal dataset is loaded. Import a USDA dataset to resolve "
                "household measures."
            ),
        )

    def nutrient_per_100g(self, food_key: str, nutrient: str, *, version: str) -> Outcome:
        """One nutrient per 100 g of a food, with provenance.

        An absent nutrient is ``Unresolved``, never ``0``. "This food contains
        no fat" and "this dataset does not record fat for this food" are
        different facts, and a nutrition panel that confuses them lies.
        """
        rows = self._query(
            "SELECT n.per_100g, n.unit, n.method, n.confidence, n.source_id, f.provider "
            "FROM nutrient n JOIN food f ON f.key = n.food_key "
            "WHERE n.food_key = ? AND n.nutrient = ?",
            (food_key, (nutrient or "").strip().lower()),
        )
        if not rows:
            return Unresolved(
                reason="no_nutrient_data",
                detail=(
                    f"The reference dataset records no {nutrient!r} for {food_key!r}. "
                    "This is not zero — import a dataset that covers this food, or "
                    "pick a closer match."
                ),
            )
        row = rows[0]
        return Resolved(
            value=float(row["per_100g"]),
            unit=row["unit"],
            provenance=self._provenance(
                row["provider"], row["source_id"], row["method"],
                float(row["confidence"]), version,
            ),
        )

    def provenance_for(self, provider: str, source_id: str, method: str,
                       confidence: float, version: str) -> Provenance:
        """Mint a ``Provenance`` citing this file's snapshot for ``provider``.

        Public because the engines need it: ``convert`` derives a weight from a
        portion row and must cite the same snapshot and licence the row came
        from. Letting an engine assemble its own provenance is how a CC0 label
        ends up on ODbL data.
        """
        return self._provenance(provider, source_id, method, confidence, version)

    def _provenance(self, provider: str, source_id: str, method: str,
                    confidence: float, version: str) -> Provenance:
        snapshot = self._source_for(provider)
        if snapshot is not None:
            return snapshot.provenance(source_id, method, version, confidence)
        # A row whose provider has no snapshot row means the dataset was built
        # by something that skipped the registry. Rather than invent a licence,
        # refuse to claim one: 'internal' is the honest label for "this file
        # told us, and the file cannot say where it got it".
        return Provenance(
            source="internal", source_id=source_id, retrieved="",
            method=method, version=version, confidence=min(confidence, 0.5),
            license="internal",
        )

    def retrieved_for(self, provider: str) -> str:
        """The snapshot date for ``provider``, or "" if this file has none."""
        snapshot = self._source_for(provider)
        return snapshot.retrieved_at if snapshot is not None else ""


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_dataset(path: str | os.PathLike[str] | None = None, *,
                 allow_minimal: bool = True) -> Dataset:
    """Open the reference dataset the host app owns.

    Resolution order: explicit ``path`` → ``$FOODCORE_DATASET`` → the bundled
    minimal fallback. There is deliberately **no** default filesystem location:
    a library guessing at ``/data`` is how two host apps end up fighting over
    one file.

    Pass ``allow_minimal=False`` to refuse the fallback — a host that has
    imported a real dataset wants a loud failure, not silently thin answers.
    """
    candidate = str(path) if path is not None else os.environ.get(DATASET_ENV_VAR, "").strip()
    if candidate:
        if not os.path.isfile(candidate):
            raise DatasetError(
                f"no foodcore dataset at {candidate!r}. Build one with "
                "`python3 -m foodcore.cli build-usda --fdc-dir <dir> --out "
                f"{candidate}`."
            )
        return Dataset(candidate)
    if not allow_minimal:
        raise DatasetError(
            "no foodcore dataset configured: pass a path to load_dataset() or set "
            f"${DATASET_ENV_VAR}. (The bundled minimal dataset was refused by "
            "allow_minimal=False.)"
        )
    return bundled_dataset()


def bundled_dataset() -> Dataset:
    """The minimal fallback: definitional facts only, no third-party data.

    Built into an in-memory SQLite database so there is exactly ONE query path
    for both the bundled and the host-owned dataset — a second code path here
    would be a second chance to behave differently in production than in tests.
    """
    try:
        from importlib import resources

        raw = (resources.files(f"{__package__}.data") / _BUNDLED_RESOURCE).read_text()
    except (OSError, ModuleNotFoundError) as exc:
        raise DatasetError(
            "the bundled minimal dataset is missing from this foodcore install "
            f"({type(exc).__name__}); reinstall the package."
        ) from None
    payload = json.loads(raw)
    records = _Records(
        sources=[SourceRecord.from_dict(s) for s in payload["sources"]],
        foods=payload["foods"],
        portions=payload["portions"],
        nutrients=payload["nutrients"],
        builder=f"bundled/{payload.get('version', '?')}",
    )
    return Dataset(
        None, is_minimal=True, _populate=lambda conn: _write(conn, records)
    )


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class _Records:
    sources: Sequence[SourceRecord]
    foods: Sequence[dict[str, Any]]
    portions: Sequence[dict[str, Any]]
    nutrients: Sequence[dict[str, Any]]
    builder: str = ""


def build_dataset(
    out_path: str | os.PathLike[str],
    *,
    sources: Iterable[SourceRecord],
    foods: Iterable[dict[str, Any]],
    portions: Iterable[dict[str, Any]],
    nutrients: Iterable[dict[str, Any]],
    builder: str = "",
) -> dict[str, int]:
    """Write a dataset file. Used by the CLI and by tests.

    Rows are plain dicts so a host app can build a dataset from a source
    foodcore has no adapter for. Required portion keys are ``food_key``,
    ``measure``, ``grams``, ``method``, ``confidence``, ``source_id``;
    ``size``, ``qualifier``, ``amount``, ``seq_num`` and ``raw_text`` are
    optional and default to "one unqualified unit", so a caller with nothing
    to say about preparation does not have to say it.

    Returns the row counts written, so a caller can report what it built rather
    than claiming success blindly. Writes to a temporary file and renames, so an
    interrupted build cannot leave the host with a half-written dataset that
    opens fine and answers wrongly.
    """
    records = _Records(
        sources=list(sources), foods=list(foods), portions=list(portions),
        nutrients=list(nutrients), builder=builder,
    )
    out_path = str(out_path)
    tmp_path = out_path + ".partial"
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)
    conn = sqlite3.connect(tmp_path)
    try:
        counts = _write(conn, records)
        conn.commit()
    except BaseException:
        conn.close()
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    finally:
        conn.close()
    os.replace(tmp_path, out_path)
    return counts


#: Columns every row of a kind must carry, and the ones that have a sensible
#: default. Declared as data so `_only` is one function rather than three.
_FOOD_REQUIRED = ("key", "name", "provider", "source_id")
_FOOD_OPTIONAL = {"data_type": ""}
_PORTION_REQUIRED = ("food_key", "measure", "grams", "method", "confidence",
                     "source_id")
_PORTION_OPTIONAL = {"size": "", "qualifier": "", "amount": 1.0, "seq_num": None,
                     "raw_text": ""}
_NUTRIENT_REQUIRED = ("food_key", "nutrient", "per_100g", "unit", "method",
                      "confidence", "source_id")


def _write(conn: sqlite3.Connection, records: _Records) -> dict[str, int]:
    """Create the schema and insert every row. Values are PARAMETERIZED — never
    interpolated into SQL text, even though they come from a local file."""
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        [("schema_version", str(_SCHEMA_VERSION)), ("builder", records.builder)],
    )
    conn.executemany(
        "INSERT INTO source (provider, dataset_version, retrieved_at, license, "
        "record_count, notes) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (r.provider, r.dataset_version, r.retrieved_at, r.license,
             r.record_count, r.notes)
            for r in records.sources
        ],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO food (key, name, provider, source_id, data_type, "
        "tokens) VALUES (:key, :name, :provider, :source_id, :data_type, :tokens)",
        [_food_row(f) for f in records.foods],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO portion (food_key, measure, size, qualifier, "
        "amount, grams, seq_num, raw_text, method, confidence, source_id) "
        "VALUES (:food_key, :measure, :size, :qualifier, :amount, :grams, "
        ":seq_num, :raw_text, :method, :confidence, :source_id)",
        [_portion_row(p) for p in records.portions],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO nutrient (food_key, nutrient, per_100g, unit, "
        "method, confidence, source_id) VALUES (:food_key, :nutrient, :per_100g, "
        ":unit, :method, :confidence, :source_id)",
        [
            _only(n, _NUTRIENT_REQUIRED, {}, lower=("nutrient",))
            for n in records.nutrients
        ],
    )
    return {
        "sources": len(records.sources),
        "foods": len(records.foods),
        "portions": len(records.portions),
        "nutrients": len(records.nutrients),
    }


def _food_row(row: dict[str, Any]) -> dict[str, Any]:
    out = _only(row, _FOOD_REQUIRED, _FOOD_OPTIONAL)
    # Derived, never caller-supplied: a token index that disagreed with the
    # name would make a food unfindable with no way to tell from the outside.
    out["tokens"] = _token_index(out["name"])
    return out


def _portion_row(row: dict[str, Any]) -> dict[str, Any]:
    out = _only(row, _PORTION_REQUIRED, _PORTION_OPTIONAL, lower=("measure",))
    # The canonical spelling, so "liter", "litres" and "l" are one row rather
    # than three. Unknown measures pass through trimmed (see units.measure_key).
    out["measure"] = measure_key(out["measure"])
    # '' rather than NULL for the two key columns: NULLs compare distinct in a
    # primary key, so a NULL size would defeat INSERT OR REPLACE and let a
    # rebuild accumulate duplicate rows.
    out["size"] = (out["size"] or "").strip().lower()
    out["qualifier"] = (out["qualifier"] or "").strip().lower()
    out["amount"] = float(out["amount"] or 1.0)
    return out


def _only(row: dict[str, Any], required: Sequence[str],
          optional: dict[str, Any], lower: Sequence[str] = ()) -> dict[str, Any]:
    """Project a row onto the columns we store, normalising lookup keys.

    Measures and nutrient names are lower-cased on the way IN so that queries
    can match exactly — normalising on the way out instead would mean every
    reader had to remember to do it.
    """
    missing = [k for k in required if k not in row]
    if missing:
        raise ValueError(f"dataset row is missing {', '.join(missing)}: {row!r}")
    out = {k: row[k] for k in required}
    for key, default in optional.items():
        out[key] = row.get(key, default)
        if out[key] is None and default is not None:
            out[key] = default
    for key in lower:
        out[key] = str(out[key]).strip().lower()
    return out
