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
| `Dataset` | `portion_grams(food_key, measure, *, version)`, `nutrient_per_100g(food_key, nutrient, *, version)` → `Outcome`; `search_foods(text, limit)` → `[FoodRow]`; `sources` → `(SourceRecord, …)`; `is_minimal`, `path`, `schema_version` |
| `FoodRow` | `key`, `name`, `provider`, `source_id` — a food as the dataset knows it |
| `SourceRecord` | one dataset snapshot as **plain frozen data**: provider, dataset version, retrieved date, licence, record count, notes. `to_dict()` / `from_dict()`, and `provenance(...)` to mint a `Provenance` that cannot disagree with the snapshot it came from |
| `build_dataset(out_path, *, sources, foods, portions, nutrients)` | write a dataset file (used by the CLI and tests) |
| `rule_version(kind)`, `RULE_EPOCH`, `__version__`, `DATASET_SCHEMA_VERSION` | versioning (see below) |
| `foodcore.convert`, `.nutrition`, `.allergens`, `.dietary` | the engines, imported lazily so `import foodcore` works without them |

`search_foods` deliberately does not decide anything: it returns candidates,
shortest name first. Ranking a user's words onto a food is policy that belongs
in an engine and in the host's own resolver.

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
unmapped — nothing is dropped silently.

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
so the tests import exactly what a host app imports. A real install needs none
of that.
