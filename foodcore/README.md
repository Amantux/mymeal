# foodcore

Shared food reference logic for **myMeal** and **Edibl**: unit conversion, USDA
portion weights, nutrition arithmetic, allergen and dietary rules — where every
answer either carries its provenance or says, explicitly, that it does not know.

It is a **library**, not a service. No container, no database server, no HTTP
port, no Home Assistant add-on. A host app installs the package and calls it
in-process.

```python
import foodcore

dataset = foodcore.load_dataset("/config/mymeal/foodcore.sqlite")
outcome = dataset.portion_grams("usda:168893", "cup",
                                version=foodcore.rule_version("convert"))

if outcome.resolved:
    print(outcome.value, outcome.unit)        # 125.0 g
    print(outcome.provenance.to_dict())       # source, method, licence, confidence…
else:
    print(outcome.reason, outcome.detail)     # "no_portion_data", "…give a weight"
```

## The one rule

**Every engine returns `Resolved` or `Unresolved`. Never `None`, and never `0`
standing in for unknown.**

"Zero grams of fat" and "we don't know the fat content" are different facts.
Conflating them is how a nutrition panel quietly lies and how an allergen filter
calls an unknown food safe, and preventing that is why this package exists.
`Unresolved` is a legitimate answer, not an error: it carries a machine-readable
`reason` your code can branch on and a one-sentence `detail` you can show a user.

```python
from foodcore import Provenance, Resolved, Unresolved

Resolved(value=125.0, unit="g", provenance=Provenance(
    source="usda",          # usda | off | foodon | internal
    source_id="168893",     # FDC id, GTIN, rule id — empty for internal defaults
    retrieved="2025-04-01", # ISO date of the dataset snapshot
    method="measured",      # measured | declared | calculated | inferred | estimated
    version="convert/1",    # which rule produced this
    confidence=0.95,        # 0.0–1.0
    license="CC0-1.0",      # CC0-1.0 | ODbL-1.0 | CC-BY-4.0 | internal
))

Unresolved(reason="no_portion_data",
           detail="No weight for 'can' of canned tomatoes; give a weight or a volume.")
```

`source`, `method` and `license` are **closed vocabularies** — constructing a
`Provenance` with a value outside them raises `VocabularyError`, so a typo can
never be stored and later mistaken for evidence. Both classes are frozen and
have `to_dict()`, whose `"status"` key (`"resolved"` / `"unresolved"`) is the
discriminator to serialise and branch on.

`license` matters downstream: USDA is CC0 (no duties), Open Food Facts is ODbL
(attribution and share-alike). The licence travels with the value because a host
app cannot work out its obligations from an aggregate number.

## Installing

```bash
pip install ./foodcore          # from this checkout
```

Zero runtime dependencies, and that is a hard rule rather than a current
accident: this installs into two host apps, so anything added here is added to
both. The package uses only the standard library (`sqlite3`, `csv`, `json`).

> **Import it as `foodcore`, never as `app`.** The source directory is named
> `app/` for historical reasons and `pyproject.toml` installs it as `foodcore`;
> both host apps already have their own top-level `app` package. Importing both
> names loads every module twice and yields two incompatible `Provenance`
> classes.

## Public API

Everything else is private and may change without notice.

| Name | What it is |
|---|---|
| `Provenance`, `Resolved`, `Unresolved`, `Outcome` | the contract above. `Outcome = Resolved \| Unresolved` is the return type to annotate |
| `VocabularyError`, `SOURCES`, `METHODS`, `LICENSES` | the closed vocabularies and the error raised on a violation |
| `load_dataset(path=None, *, allow_minimal=True)` | open the reference dataset (see below) |
| `Dataset` | the **one** reference dataset (see below). `portion_grams(food_key, measure, *, version)`, `nutrient_per_100g(food_key, nutrient, *, version)` → `Outcome`; `food_record(key)` → `FoodRecord`; `resolve_food(text)` → `FoodMatch`; `search_foods(text, limit)` → `[FoodRow]`; `food(key)` → `FoodRow`; `provenance_for(...)`, `retrieved_for(provider)`; `sources` → `(SourceRecord, …)`; `is_minimal`, `path`, `schema_version` |
| `FoodRow` | `key`, `name`, `provider`, `source_id` — a **candidate**, without measurements. What `search_foods` returns |
| `FoodRecord` | a food **with** its `portions` and `nutrients`, plus `head` / `tokens` / `states` and the selection helpers `portion(measure, size=None)`, `sized_portions`, `qualified_portions`, `volume_portion`. What the engines take |
| `Portion` | one measured portion: `measure`, `size`, `qualifier`, `amount`, `gram_weight`, `grams_per_unit`, `method`, `confidence`, `source_id`, `raw_text` |
| `FoodMatch` | `query`, `confident`, `candidates` → `((FoodRecord, score), …)`, `food` (None unless confident), `describe(limit)` |
| `SourceRecord` | one dataset snapshot as **plain frozen data**: provider, dataset version, retrieved date, licence, record count, notes. `to_dict()` / `from_dict()`, and `provenance(...)` to mint a `Provenance` that cannot disagree with the snapshot it came from |
| `build_dataset(out_path, *, sources, foods, portions, nutrients)` | write a dataset file (used by the CLI and tests) |
| `rule_version(kind)`, `RULE_EPOCH`, `__version__`, `DATASET_SCHEMA_VERSION` | versioning (see below) |
| `foodcore.convert`, `.nutrition`, `.allergens`, `.dietary` | the engines, imported lazily so `import foodcore` works without them |

Two ways to get from a name to a food, and they are not interchangeable:

* **`search_foods(text)`** decides nothing. Substring match, shortest name
  first — candidates for a human to pick from.
* **`resolve_food(text)`** applies the confidence-tiered policy and **refuses
  rather than guesses**. Candidates must contain *every* token of the query;
  a match is confident only when it is a dataset key, a provider id, the lone
  result, or beats the runner-up by a wide margin. So `"rice"` comes back
  `confident=False` with raw rice and cooked rice as candidates (185 g vs 158 g
  per cup), while `"cooked rice"` resolves. Check `confident` before using
  `.food`; show `describe()` when you cannot.

`FoodRow` vs `FoodRecord` is a real distinction, not duplication: a `FoodRecord`
whose `portions` is empty means *this food publishes no portions*, and a
candidate list must not say that about every candidate. Ask for
`food_record(key)` when you need the measurements.

## Reference data: the host owns the file

foodcore never looks in a hardcoded location — a library guessing at `/data` is
how two host apps end up fighting over one file. Resolution order:

1. the path you pass to `load_dataset(path)` — **use this**; your app knows its
   own data dir;
2. `$FOODCORE_DATASET`, for a host that configures by environment;
3. a tiny **bundled fallback**, so `import foodcore` and your test suite work
   with no setup at all.

The fallback holds definitional facts only (US customary volume sizes and the
water gram/millilitre identity), labelled `source="internal"`,
`method="inferred"`. It contains **no USDA or Open Food Facts data**: shipping
plausible-looking nutrient numbers under a `measured`/`CC0-1.0` label would be
fabricated provenance. Check `dataset.is_minimal` and tell your user their
reference data has not been imported yet.

Pass `allow_minimal=False` if your app has imported real data and wants a loud
failure instead of silently thin answers.

### One dataset, one loader

There is exactly one `Dataset` class and one way to build a dataset file. The
layers are split by responsibility, not by storage:

| Layer | Module | Owns |
|---|---|---|
| vocabulary | `units.py` | unit spellings, sizes, tokenisation — one table for the whole package |
| parsing | `sources/usda.py` | reading **one** provider's export into `FoodRecord` / `Portion` |
| storage + lookup | `datasets.py` | the record types, the file format, and reading it back |

A source adapter parses; it does not store, and it does not define its own
`Dataset`. Adding a provider (Open Food Facts next) is a new module under
`sources/` that returns the same records — not a second dataset type.

**Why SQLite**: the data is tens of megabytes of mostly-cold rows queried by
key. In-memory JSON would be hundreds of MB resident in every worker of every
host app; a database server is the deployment this package was made a library to
avoid. One file gives random access without loading the dataset, lives in the
host's own data dir, is backed up with everything else — and `sqlite3` is in the
standard library, so it costs no dependency.

**The bulk dataset is never committed.** Build it on the host:

```bash
python3 -m foodcore.cli build-usda \
    --fdc-dir ./FoodData_Central_csv \
    --out /config/mymeal/foodcore.sqlite \
    --dataset-version "April 2025" --retrieved 2025-04-01

python3 -m foodcore.cli info --dataset /config/mymeal/foodcore.sqlite
```

`--dataset-version` is required: a snapshot that cannot say which release it is
cannot be cited, and an uncitable value is one this package will not serve. The
build reports what it wrote, what it skipped, and which upstream nutrients were
unmapped — nothing is dropped silently, and that report is stored on the
snapshot's `SourceRecord.notes` so months later you can still see what the
dataset does *not* contain.

**A dataset version is a file, not a row.** `build_dataset` writes to a
temporary path and renames, so a build either replaces the dataset atomically
or leaves it alone; keep the previous file if you want the previous snapshot.
Rebuilding from the same export produces a byte-identical file, which is what
makes "re-importing changes nothing" checkable rather than merely claimed.
Several *providers* can coexist inside one file, and each value's provenance
names the snapshot and licence it came from.

### The two USDA traps the importer handles

Both are in the published schema, both silently corrupt a hand-written reader,
and both have tests against real rows:

1. **The unit is not always in `measure_unit_id`.** SR Legacy parks every
   portion on id 9999 ("undetermined") and writes the unit as free text in
   `modifier` (`"cup, chopped"`, `"stalk, medium (7-1/2\" long)"`); Foundation
   Foods does the opposite. Read one column only and most of the dataset's
   portions vanish.
2. **`amount` is not always 1.** `amount=3, modifier="cloves", gram_weight=9`
   is 3 g per clove, not 9. Both numbers are stored as published and
   `Portion.grams_per_unit` does the division, so a reader can still see what
   upstream said.

A consequence of the first: the preparation qualifier has to survive *storage*
too. Onions publish `cup, chopped` 160 g and `cup, sliced` 115 g and no plain
cup, so the `portion` table is keyed on `(food_key, measure, size, qualifier,
source_id)`. Flatten that to one `measure` string and `"1 cup onion"` silently
answers with whichever row was written last — a 39% error. foodcore instead
returns `Unresolved("qualifier_required")` and names the choices.

## Persistence is yours

`SourceRecord` is a frozen dataclass, not an ORM model. If you want the snapshot
registry in your own schema, map `to_dict()` into your table; if you don't, ignore
it. An ORM base class here would drag SQLAlchemy into every consumer.

## Versioning

- `foodcore.__version__` — the package version.
- `foodcore.RULE_EPOCH` — the number in every `Provenance.version` string.
  `rule_version("convert")` → `"convert/1"`. Use it instead of hardcoding the
  string, so the whole package moves together and a host app can tell which
  foodcore computed a value it stored. It is **not** the package version: a
  patch release must not invalidate stored provenance. Bump it only when an
  algorithm's *output* changes, and note the change here.
- `foodcore.DATASET_SCHEMA_VERSION` — the dataset file layout. A `Dataset`
  refuses to open a file from a *future* schema rather than mis-reading it.

### Rule history

| Epoch | What changed |
|---|---|
| 1 | Initial release. |

Dataset schema history:

| Schema | What changed |
|---|---|
| 1 | Initial layout. Portions keyed `(food_key, measure)`. |
| 2 | Portions carry `size`, `qualifier`, `amount`, `seq_num`, `raw_text` and are keyed on all of them, so two preparations of one measure no longer collide; foods carry a token index for `resolve_food`. Rebuild with `build-usda`; a schema-1 file is refused rather than mis-read. |

## Extraction

Lifting this directory into its own repository must not change one import line:
there are no imports reaching into a host app, no absolute paths outside the
package, and all intra-package imports are relative. `tests/test_package.py`
enforces that structurally, including for modules added later.

## Development

```bash
cd foodcore
python3 -m pytest -q      # works from the checkout; no install needed
ruff check .
```

`conftest.py` registers the `app/` directory under the single name `foodcore`
so the tests import exactly what a host app imports — **unconditionally**, so a
copy of foodcore already installed on the machine cannot shadow the checkout and
leave you testing code you are not editing.

### The install acceptance test

The checkout suite cannot see packaging defects: a subpackage missing from
`pyproject.toml`'s `packages` list is right there on disk and only vanishes from
an installed wheel. Run this before calling any foodcore change done:

```bash
python3 -m venv /tmp/fc && /tmp/fc/bin/pip -q install ./foodcore
/tmp/fc/bin/python - <<'EOF'
import importlib, foodcore
for banned in ("flask", "sqlalchemy", "alembic"):
    assert importlib.util.find_spec(banned) is None, banned
for sub in ("datasets", "units", "cli", "sources.usda", "rules.allergens_v1"):
    importlib.import_module(f"foodcore.{sub}")
d = foodcore.load_dataset()                       # bundled fallback
print(d.portion_grams("internal:water", "cup", version=foodcore.rule_version("convert")))
EOF
```
