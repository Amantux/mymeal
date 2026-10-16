"""Reference-data loading. The host app owns the file; foodcore only reads it.

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
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from .provenance import Outcome, Provenance, Resolved, Unresolved
from .registry import SourceRecord

#: Set by a host app that configures through the environment. Checked only when
#: no explicit path is given — never as a default location.
DATASET_ENV_VAR = "FOODCORE_DATASET"

_BUNDLED_RESOURCE = "bundled.json"

# Bumped when the table layout below changes incompatibly. A Dataset refuses to
# open a file from a FUTURE schema rather than silently mis-reading it.
_SCHEMA_VERSION = 1

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
    source_id TEXT NOT NULL
);
CREATE INDEX food_name ON food (name);

CREATE TABLE portion (
    food_key   TEXT NOT NULL REFERENCES food (key),
    measure    TEXT NOT NULL,     -- normalised: "cup", "tablespoon", "slice"
    grams      REAL NOT NULL,
    method     TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_id  TEXT NOT NULL,
    PRIMARY KEY (food_key, measure)
);

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


@dataclass(frozen=True)
class FoodRow:
    """A food as the dataset knows it. Plain data — the host maps it to its own
    concept of a food; foodcore does not own anybody's food catalogue."""

    key: str
    name: str
    provider: str
    source_id: str


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
        self._sources = self._read_sources()
        self._schema_version = int(self._meta().get("schema_version", 0))
        if self._schema_version > _SCHEMA_VERSION:
            raise DatasetError(
                f"{self._describe()} was built with dataset schema "
                f"{self._schema_version}, but this foodcore understands at most "
                f"{_SCHEMA_VERSION}. Upgrade foodcore, or rebuild the dataset."
            )

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

        Deliberately dumb: *ranking* a user's words onto a food is policy that
        belongs in an engine (and in the host's own resolver), not in the data
        layer. This returns candidates; it does not decide.
        """
        needle = (text or "").strip().lower()
        if not needle:
            return []
        rows = self._query(
            "SELECT key, name, provider, source_id FROM food "
            "WHERE lower(name) LIKE ? ORDER BY length(name), name LIMIT ?",
            (f"%{needle}%", max(1, int(limit))),
        )
        return [FoodRow(**dict(row)) for row in rows]

    def portion_grams(self, food_key: str, measure: str, *, version: str) -> Outcome:
        """Grams for one household measure of a food, with provenance.

        ``version`` is the caller's rule version (``foodcore.rule_version(…)``)
        so the returned provenance records which engine asked.
        """
        rows = self._query(
            "SELECT p.grams, p.method, p.confidence, p.source_id, f.provider "
            "FROM portion p JOIN food f ON f.key = p.food_key "
            "WHERE p.food_key = ? AND p.measure = ?",
            (food_key, (measure or "").strip().lower()),
        )
        if not rows:
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
        row = rows[0]
        return Resolved(
            value=float(row["grams"]),
            unit="g",
            provenance=self._provenance(
                row["provider"], row["source_id"], row["method"],
                float(row["confidence"]), version,
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
    for stale in (tmp_path,):
        if os.path.exists(stale):
            os.unlink(stale)
    conn = sqlite3.connect(tmp_path)
    try:
        counts = _write(conn, records)
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp_path, out_path)
    return counts


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
        "INSERT OR REPLACE INTO food (key, name, provider, source_id) "
        "VALUES (:key, :name, :provider, :source_id)",
        [_only(f, ("key", "name", "provider", "source_id")) for f in records.foods],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO portion (food_key, measure, grams, method, "
        "confidence, source_id) VALUES (:food_key, :measure, :grams, :method, "
        ":confidence, :source_id)",
        [
            _only(p, ("food_key", "measure", "grams", "method", "confidence",
                      "source_id"), lower=("measure",))
            for p in records.portions
        ],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO nutrient (food_key, nutrient, per_100g, unit, "
        "method, confidence, source_id) VALUES (:food_key, :nutrient, :per_100g, "
        ":unit, :method, :confidence, :source_id)",
        [
            _only(n, ("food_key", "nutrient", "per_100g", "unit", "method",
                      "confidence", "source_id"), lower=("nutrient",))
            for n in records.nutrients
        ],
    )
    return {
        "sources": len(records.sources),
        "foods": len(records.foods),
        "portions": len(records.portions),
        "nutrients": len(records.nutrients),
    }


def _only(row: dict[str, Any], keys: Sequence[str],
          lower: Sequence[str] = ()) -> dict[str, Any]:
    """Project a row onto the columns we store, normalising lookup keys.

    Measures and nutrient names are lower-cased on the way IN so that queries
    can match exactly — normalising on the way out instead would mean every
    reader had to remember to do it.
    """
    missing = [k for k in keys if k not in row]
    if missing:
        raise ValueError(f"dataset row is missing {', '.join(missing)}: {row!r}")
    out = {k: row[k] for k in keys}
    for key in lower:
        out[key] = str(out[key]).strip().lower()
    return out
