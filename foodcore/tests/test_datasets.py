"""Dataset loading behaviour, exercised through the public surface a host app
and the engines use: ``load_dataset`` / ``Dataset`` / ``build_dataset``."""
import os

import pytest

from foodcore import SourceRecord, build_dataset, load_dataset, rule_version
from foodcore.datasets import DATASET_ENV_VAR, DatasetError, bundled_dataset

VERSION = rule_version("convert")


def write_dataset(path, *, provider="usda", license="CC0-1.0"):
    """A small real dataset file, built through the public writer."""
    record = SourceRecord(
        provider=provider,
        dataset_version="April 2025",
        retrieved_at="2025-04-01",
        license=license,
        record_count=1,
    )
    build_dataset(
        path,
        sources=[record],
        foods=[{"key": f"{provider}:1", "name": "Flour, wheat, white",
                "provider": provider, "source_id": "1"}],
        portions=[{"food_key": f"{provider}:1", "measure": "Cup", "grams": 125.0,
                   "method": "measured", "confidence": 0.95, "source_id": "1"}],
        nutrients=[{"food_key": f"{provider}:1", "nutrient": "Energy",
                    "per_100g": 364.0, "unit": "kcal", "method": "measured",
                    "confidence": 0.95, "source_id": "1"}],
        builder="test",
    )
    return path


# --- resolution order ---------------------------------------------------

def test_explicit_path_is_used_when_given(tmp_path):
    path = write_dataset(str(tmp_path / "fc.sqlite"))

    dataset = load_dataset(path)

    assert dataset.path == path
    assert dataset.is_minimal is False


def test_env_var_is_used_when_no_path_is_passed(tmp_path, monkeypatch):
    path = write_dataset(str(tmp_path / "fc.sqlite"))
    monkeypatch.setenv(DATASET_ENV_VAR, path)

    dataset = load_dataset()

    assert dataset.path == path


def test_explicit_path_beats_the_env_var(tmp_path, monkeypatch):
    explicit = write_dataset(str(tmp_path / "explicit.sqlite"))
    monkeypatch.setenv(DATASET_ENV_VAR, str(tmp_path / "from-env.sqlite"))

    assert load_dataset(explicit).path == explicit


def test_no_configuration_falls_back_to_the_bundled_minimal_dataset(monkeypatch):
    monkeypatch.delenv(DATASET_ENV_VAR, raising=False)

    dataset = load_dataset()

    # So that `import foodcore` works out of the box, and a host app can tell
    # the user their real reference data has not been imported yet.
    assert dataset.is_minimal is True


def test_allow_minimal_false_refuses_the_fallback(monkeypatch):
    monkeypatch.delenv(DATASET_ENV_VAR, raising=False)

    with pytest.raises(DatasetError):
        load_dataset(allow_minimal=False)


def test_missing_file_names_the_path_and_the_fix(tmp_path):
    with pytest.raises(DatasetError) as exc:
        load_dataset(str(tmp_path / "absent.sqlite"))

    message = str(exc.value)
    assert "absent.sqlite" in message
    assert "build-usda" in message


def test_library_never_guesses_a_default_location(monkeypatch, tmp_path):
    # A library that guesses at /data is how two host apps end up fighting over
    # one file. With nothing configured we get the bundled dataset, never a
    # filesystem probe.
    monkeypatch.delenv(DATASET_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    assert load_dataset().path is None


# --- lookups return Resolved | Unresolved -------------------------------

def test_portion_lookup_resolves_with_provenance_from_the_snapshot(tmp_path):
    dataset = load_dataset(write_dataset(str(tmp_path / "fc.sqlite")))

    outcome = dataset.portion_grams("usda:1", "cup", version=VERSION)

    assert outcome.resolved is True
    assert outcome.value == 125.0
    assert outcome.unit == "g"
    assert outcome.provenance.source == "usda"
    assert outcome.provenance.license == "CC0-1.0"
    assert outcome.provenance.retrieved == "2025-04-01"
    assert outcome.provenance.version == VERSION


def test_measure_lookup_is_case_insensitive(tmp_path):
    dataset = load_dataset(write_dataset(str(tmp_path / "fc.sqlite")))

    assert dataset.portion_grams("usda:1", "CUP", version=VERSION).resolved is True


def test_unknown_portion_is_unresolved_not_zero(tmp_path):
    dataset = load_dataset(write_dataset(str(tmp_path / "fc.sqlite")))

    outcome = dataset.portion_grams("usda:1", "can", version=VERSION)

    assert outcome.resolved is False
    assert outcome.reason == "no_portion_data"
    assert outcome.detail  # says what would fix it
    assert not hasattr(outcome, "value")


def test_nutrient_lookup_resolves_with_its_unit(tmp_path):
    dataset = load_dataset(write_dataset(str(tmp_path / "fc.sqlite")))

    outcome = dataset.nutrient_per_100g("usda:1", "energy", version=VERSION)

    assert (outcome.value, outcome.unit) == (364.0, "kcal")


def test_absent_nutrient_is_unresolved_not_zero(tmp_path):
    dataset = load_dataset(write_dataset(str(tmp_path / "fc.sqlite")))

    outcome = dataset.nutrient_per_100g("usda:1", "protein", version=VERSION)

    # "Contains no protein" and "this dataset does not record protein" are
    # different facts; a panel that returns 0 for the second one lies.
    assert outcome.resolved is False
    assert outcome.reason == "no_nutrient_data"


def test_provenance_license_follows_the_snapshot_not_the_caller(tmp_path):
    path = write_dataset(str(tmp_path / "off.sqlite"), provider="off",
                         license="ODbL-1.0")

    outcome = load_dataset(path).portion_grams("off:1", "cup", version=VERSION)

    # Attribution duties differ by source, so the licence must travel with the
    # value rather than being assumed CC0 by whoever reads it.
    assert outcome.provenance.license == "ODbL-1.0"
    assert outcome.provenance.source == "off"


# --- candidates, sources, schema ---------------------------------------

def test_search_returns_candidates_without_deciding(tmp_path):
    dataset = load_dataset(write_dataset(str(tmp_path / "fc.sqlite")))

    rows = dataset.search_foods("flour")

    assert [row.key for row in rows] == ["usda:1"]


def test_search_with_empty_text_returns_nothing(tmp_path):
    dataset = load_dataset(write_dataset(str(tmp_path / "fc.sqlite")))

    assert dataset.search_foods("   ") == []


def test_sources_are_plain_records_the_host_may_persist(tmp_path):
    dataset = load_dataset(write_dataset(str(tmp_path / "fc.sqlite")))

    assert [s.to_dict()["datasetVersion"] for s in dataset.sources] == ["April 2025"]


def test_a_future_schema_version_is_refused(tmp_path):
    path = write_dataset(str(tmp_path / "fc.sqlite"))
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    # Refusing beats mis-reading a layout we do not understand.
    with pytest.raises(DatasetError) as exc:
        load_dataset(path)
    assert "99" in str(exc.value)


def test_a_corrupt_file_raises_a_curated_error_not_driver_text(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    path.write_bytes(b"this is not a database")

    with pytest.raises(DatasetError) as exc:
        load_dataset(str(path))
    message = str(exc.value)
    assert "corrupt.sqlite" in message
    # MUTATION-CHECKED 2026-10-16. The previous assertion was
    #     assert "sqlite3" not in message.lower() or "build-usda" in message
    # whose right-hand side is ALWAYS true (every curated message ends with
    # "Rebuild it with `python3 -m foodcore.cli build-usda`"), so the left-hand
    # side never evaluated. Interpolating the driver's own text into _query's
    # message left it passing. These assertions fail when that happens.
    assert "not a database" not in message        # sqlite3's own message text
    assert "build-usda" in message                # ...but the fix IS named
    # The exception CLASS name is deliberate and carries no data — it is how an
    # operator tells a corrupt file from a permissions problem. The driver's
    # message is what must never appear, because that is where a DSN or a data
    # row would ride along.


def test_an_interrupted_build_leaves_no_half_written_dataset(tmp_path):
    """MUTATION-CHECKED 2026-10-16.

    The previous version asserted `not (...).exists() or True`, which is
    unconditionally true, and `not path.exists()`, which passes with no cleanup
    code at all because `os.replace` is never reached on the failure path.
    Deleting the whole `except BaseException` block left both passing.

    What actually has to hold is that NO file is left in the directory — the
    temporary name is now unique, so naming it in the test would be guessing.
    """
    path = tmp_path / "fc.sqlite"
    with pytest.raises(ValueError):
        build_dataset(
            path,
            sources=[],
            foods=[{"key": "usda:1"}],  # missing columns -> raises mid-build
            portions=[],
            nutrients=[],
        )

    assert not path.exists()
    assert list(tmp_path.iterdir()) == [], "a partial dataset was left behind"


def test_an_unwritable_destination_is_a_typed_error(tmp_path):
    # The CLI prints whatever escapes build_dataset. A raw
    # sqlite3.OperationalError traceback is useless to an operator and is a
    # place for a path or a data row to leak.
    with pytest.raises(DatasetError) as exc:
        build_dataset(
            tmp_path / "no_such_dir" / "fc.sqlite",
            sources=[], foods=[], portions=[], nutrients=[],
        )

    message = str(exc.value)
    assert "sqlite3" not in message.lower()
    assert "unable to open" not in message.lower()
    assert "writable" in message or "exists" in message


def test_build_reports_what_it_wrote(tmp_path):
    counts = build_dataset(
        tmp_path / "fc.sqlite",
        sources=[SourceRecord("internal", "v1", "2026-01-01", "internal")],
        foods=[{"key": "internal:x", "name": "x", "provider": "internal",
                "source_id": "x"}],
        portions=[],
        nutrients=[],
    )

    assert counts == {"sources": 1, "foods": 1, "portions": 0, "nutrients": 0}


# --- the bundled fallback ----------------------------------------------

def test_bundled_dataset_resolves_a_definitional_volume():
    outcome = bundled_dataset().portion_grams("internal:water", "cup", version=VERSION)

    assert outcome.resolved is True
    assert round(outcome.value, 3) == 236.588


def test_bundled_dataset_claims_no_third_party_licence():
    # It ships definitional facts only. Labelling a remembered number as
    # USDA/CC0 would be fabricated provenance.
    dataset = bundled_dataset()

    assert [s.provider for s in dataset.sources] == ["internal"]
    assert [s.license for s in dataset.sources] == ["internal"]
    outcome = dataset.portion_grams("internal:water", "cup", version=VERSION)
    assert outcome.provenance.method == "inferred"
    assert outcome.provenance.license == "internal"


def test_bundled_dataset_says_so_when_it_cannot_help():
    outcome = bundled_dataset().portion_grams("usda:1", "cup", version=VERSION)

    assert outcome.resolved is False
    assert "minimal" in outcome.detail


def test_bundled_dataset_is_usable_from_several_threads():
    # A host app serves requests on gunicorn threads, and sqlite3 connections
    # must not be shared across them.
    import threading

    dataset = bundled_dataset()
    results = []

    def lookup():
        results.append(dataset.portion_grams("internal:water", "liter",
                                             version=VERSION).value)

    threads = [threading.Thread(target=lookup) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [1000.0] * 4


def test_the_bulk_dataset_is_not_committed():
    # The bundled JSON is tiny on purpose; a committed USDA export would be
    # tens of megabytes of third-party data in the host app's repository.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(here, "app", "data", "bundled.json")

    assert os.path.getsize(bundled) < 16 * 1024


# --- the storage layer must not flatten what the parser worked out ------

def write_onion(path):
    """The real trap, at dataset-file scale.

    USDA 170000 publishes 'cup, chopped' 160 g and 'cup, sliced' 115 g and no
    plain cup. Both rows name the measure 'cup', so a `portion` table keyed on
    (food_key, measure) alone keeps whichever was written last and answers
    115 g or 160 g to "1 cup onion" — a 39% silent error either way.
    """
    build_dataset(
        path,
        sources=[SourceRecord("usda", "April 2025", "2025-04-01", "CC0-1.0")],
        foods=[{"key": "usda:170000", "name": "Onions, raw", "provider": "usda",
                "source_id": "170000"}],
        portions=[
            {"food_key": "usda:170000", "measure": "cup", "qualifier": "chopped",
             "grams": 160.0, "method": "measured", "confidence": 0.95,
             "source_id": "p1"},
            {"food_key": "usda:170000", "measure": "cup", "qualifier": "sliced",
             "grams": 115.0, "method": "measured", "confidence": 0.95,
             "source_id": "p2"},
            {"food_key": "usda:170000", "measure": "piece", "size": "medium",
             "grams": 110.0, "method": "measured", "confidence": 0.95,
             "source_id": "p3"},
        ],
        nutrients=[],
    )
    return path


def test_two_preparations_of_one_measure_both_survive_the_write(tmp_path):
    dataset = load_dataset(write_onion(str(tmp_path / "fc.sqlite")))

    record = dataset.food_record("usda:170000")

    grams = sorted(p.gram_weight for p in record.qualified_portions("cup"))
    assert grams == [115.0, 160.0], "a qualifier must be part of the portion key"


def test_a_bare_measure_is_refused_when_only_prepared_rows_exist(tmp_path):
    dataset = load_dataset(write_onion(str(tmp_path / "fc.sqlite")))

    outcome = dataset.portion_grams("usda:170000", "cup", version=VERSION)

    assert outcome.resolved is False
    assert outcome.reason == "qualifier_required"
    assert "chopped" in outcome.detail and "sliced" in outcome.detail


def test_a_bare_measure_is_refused_when_only_sized_rows_exist(tmp_path):
    dataset = load_dataset(write_onion(str(tmp_path / "fc.sqlite")))

    outcome = dataset.portion_grams("usda:170000", "piece", version=VERSION)

    # "1 onion" is under-specified in the same way "1 cup onion" is: the dataset
    # knows medium, and standing it in without saying so is the fabrication.
    assert outcome.reason == "size_required"
    assert "medium" in outcome.detail


def test_an_unknown_measure_is_still_no_portion_data(tmp_path):
    dataset = load_dataset(write_onion(str(tmp_path / "fc.sqlite")))

    outcome = dataset.portion_grams("usda:170000", "fillet", version=VERSION)

    assert outcome.reason == "no_portion_data"


def test_the_published_amount_survives_the_write(tmp_path):
    path = tmp_path / "fc.sqlite"
    build_dataset(
        path,
        sources=[SourceRecord("usda", "v1", "2025-04-01", "CC0-1.0")],
        foods=[{"key": "usda:1", "name": "Garlic, raw", "provider": "usda",
                "source_id": "1"}],
        portions=[{"food_key": "usda:1", "measure": "clove", "amount": 3.0,
                   "grams": 9.0, "method": "measured", "confidence": 0.95,
                   "source_id": "p1"}],
        nutrients=[],
    )

    outcome = load_dataset(str(path)).portion_grams("usda:1", "clove",
                                                    version=VERSION)

    # "3 cloves = 9 g" is 3 g per clove. Returning 9 is wrong by 3x.
    assert outcome.value == 3.0


@pytest.mark.parametrize("spelling", ["liter", "Litres", "l", "LITER"])
def test_measure_spellings_land_on_one_row(spelling):
    # The bundled file spells it "liter"; a host app may ask for "l". One
    # vocabulary (units.measure_key) is applied on the way in AND on the way
    # out, so these are the same row rather than four.
    outcome = bundled_dataset().portion_grams("internal:water", spelling,
                                              version=VERSION)

    assert outcome.resolved is True
    assert outcome.value == 1000.0


def test_search_treats_like_wildcards_as_literal_text(tmp_path):
    dataset = load_dataset(write_dataset(str(tmp_path / "fc.sqlite")))

    # Unescaped, '_' matches any character and '%' matches everything, so a
    # query containing one would silently return unrelated foods.
    assert dataset.search_foods("flou_") == []
    assert dataset.search_foods("%") == []


# --- idempotence and versioning, at the layer that now owns them --------

def build_fixture(path, *, version="v1", retrieved="2026-01-01"):
    from foodcore.sources.usda import load_fdc_csv

    from .conftest import FIXTURE_DIR

    export = load_fdc_csv(FIXTURE_DIR, version=version, retrieved=retrieved)
    foods, portions, nutrients = export.rows()
    build_dataset(path, sources=[export.source_record()], foods=foods,
                  portions=portions, nutrients=nutrients, builder="test")
    return path


def test_rebuilding_the_same_export_yields_an_identical_file(tmp_path):
    """Spec §14's idempotent import, stated at the layer that now stores data.

    The in-memory `ReferenceStore` this replaces asserted idempotence by
    counting dict inserts, which can only ever prove that a dict does not grow.
    The deployable claim is that re-running a build leaves the host with the
    same dataset, and byte equality is the strongest available form of it.
    """
    first = build_fixture(str(tmp_path / "a.sqlite"))
    second = build_fixture(str(tmp_path / "b.sqlite"))

    with open(first, "rb") as a, open(second, "rb") as b:
        assert a.read() == b.read()


def test_rebuilding_in_place_does_not_accumulate_rows(tmp_path):
    """Keyed on the DATA, not on the import run: the primary keys are
    `provider:source_id` and (food_key, measure, size, qualifier, source_id),
    so a second write of a row replaces the first rather than adding one."""
    path = str(tmp_path / "fc.sqlite")
    build_fixture(path)
    before = load_dataset(path)
    counts = (len(before.food_record("usda:170000").portions),
              len(before.search_foods("onion", limit=100)))

    build_fixture(path)
    after = load_dataset(path)

    assert (len(after.food_record("usda:170000").portions),
            len(after.search_foods("onion", limit=100))) == counts


def test_a_build_replaces_the_dataset_atomically(tmp_path):
    """A new snapshot is a new FILE, and the swap is a rename. A host that
    wants to keep the old snapshot keeps the old file; what must never happen
    is a half-written dataset that opens fine and answers wrongly."""
    path = str(tmp_path / "fc.sqlite")
    build_fixture(path, version="v1", retrieved="2026-01-01")
    build_fixture(path, version="v2", retrieved="2026-06-01")

    dataset = load_dataset(path)

    assert [s.dataset_version for s in dataset.sources] == ["v2"]
    assert not os.path.exists(path + ".partial")


def test_several_provider_snapshots_coexist_in_one_file(tmp_path):
    path = tmp_path / "fc.sqlite"
    build_dataset(
        path,
        sources=[
            SourceRecord("usda", "April 2025", "2025-04-01", "CC0-1.0"),
            SourceRecord("off", "2026-01", "2026-01-15", "ODbL-1.0"),
        ],
        foods=[{"key": "usda:1", "name": "Flour", "provider": "usda",
                "source_id": "1"},
               {"key": "off:1", "name": "Branded flour", "provider": "off",
                "source_id": "1"}],
        portions=[{"food_key": "off:1", "measure": "cup", "grams": 120.0,
                   "method": "declared", "confidence": 0.7, "source_id": "x"}],
        nutrients=[],
    )

    outcome = load_dataset(str(path)).portion_grams("off:1", "cup",
                                                    version=VERSION)

    # The licence travels with the value: an ODbL row in a file that also holds
    # CC0 rows must not inherit CC0.
    assert outcome.provenance.license == "ODbL-1.0"
    assert outcome.provenance.source == "off"


def test_an_id_two_providers_both_use_is_not_an_identifier(tmp_path):
    path = tmp_path / "fc.sqlite"
    build_dataset(
        path,
        sources=[SourceRecord("usda", "v1", "2025-04-01", "CC0-1.0"),
                 SourceRecord("off", "v1", "2026-01-15", "ODbL-1.0")],
        foods=[{"key": "usda:1", "name": "Flour, wheat", "provider": "usda",
                "source_id": "1"},
               {"key": "off:1", "name": "Flour, branded", "provider": "off",
                "source_id": "1"}],
        portions=[], nutrients=[],
    )

    match = load_dataset(str(path)).resolve_food("1")

    # Resolving "1" to whichever row came first would attribute an answer to a
    # provider at random. Ambiguity falls through to scoring, which refuses.
    assert match.food is None


def test_reference_data_has_no_tenant_column(tmp_path):
    """A tenant key in reference data would be a modelling error: a gram of
    flour is the same gram in every household."""
    import sqlite3

    path = build_fixture(str(tmp_path / "fc.sqlite"))
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")]
        columns = {c[1] for t in tables
                   for c in conn.execute(f"PRAGMA table_info({t})")}
    finally:
        conn.close()

    assert not {"group_id", "owner_id", "household_id", "user_id"} & columns


def test_a_dataset_from_an_older_schema_is_refused(tmp_path):
    path = write_dataset(str(tmp_path / "fc.sqlite"))
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    # Schema 1 flattened size and qualifier into the measure name. Reading one
    # as if it were schema 2 would answer with whichever prepared row survived
    # the old primary key — a plausible wrong number, which is worse than a
    # refusal that says "rebuild".
    with pytest.raises(DatasetError) as exc:
        load_dataset(path)
    assert "rebuild" in str(exc.value).lower()


# --- the writer must not claim more than it stored ----------------------

def test_build_counts_come_from_the_file_not_the_input(tmp_path):
    """MUTATION GUARD for the `build_dataset` return contract.

    `INSERT OR REPLACE` collapses rows sharing a primary key. Returning
    `len(portions)` reports what the caller HANDED IN, which is a different
    fact from what the host now has — and the difference is silently-lost
    portion weights. Two rows below differ only in `grams`, which is NOT part
    of the key, so exactly one survives and the count must say 1.
    """
    counts = build_dataset(
        tmp_path / "fc.sqlite",
        sources=[SourceRecord("usda", "v1", "2025-04-01", "CC0-1.0")],
        foods=[{"key": "usda:1", "name": "Flour", "provider": "usda",
                "source_id": "1"}],
        portions=[
            {"food_key": "usda:1", "measure": "cup", "grams": 125.0,
             "method": "measured", "confidence": 0.95, "source_id": "same"},
            {"food_key": "usda:1", "measure": "cup", "grams": 160.0,
             "method": "measured", "confidence": 0.95, "source_id": "same"},
        ],
        nutrients=[],
    )

    assert counts["portions"] == 1, "the writer reported rows it did not store"


def test_a_portion_with_no_source_id_is_refused(tmp_path):
    """`source_id` is the last column of the portion primary key.

    An FDC export whose `food_portion.csv` lacks its `id` column gives every
    row a blank one, so all of a food's portions collide and only the last
    survives — 8 of 157 measured weights disappeared this way. A portion with
    no identity could not be cited in a Provenance anyway, so refuse it.
    """
    with pytest.raises(ValueError) as exc:
        build_dataset(
            tmp_path / "fc.sqlite",
            sources=[SourceRecord("usda", "v1", "2025-04-01", "CC0-1.0")],
            foods=[{"key": "usda:1", "name": "Flour", "provider": "usda",
                    "source_id": "1"}],
            portions=[{"food_key": "usda:1", "measure": "cup", "grams": 125.0,
                       "method": "measured", "confidence": 0.95,
                       "source_id": "  "}],
            nutrients=[],
        )

    assert "source_id" in str(exc.value)


@pytest.mark.parametrize("amount", [0, 0.0, -1.0])
def test_a_nonpositive_portion_amount_is_refused(tmp_path, amount):
    # `grams` is measured FOR `amount` of the measure, so grams/amount is the
    # per-unit weight. Coercing a 0 to 1.0 fabricates a weight from a row that
    # says nothing — the exact class of defect this package exists to prevent.
    with pytest.raises(ValueError) as exc:
        build_dataset(
            tmp_path / "fc.sqlite",
            sources=[SourceRecord("usda", "v1", "2025-04-01", "CC0-1.0")],
            foods=[{"key": "usda:1", "name": "Flour", "provider": "usda",
                    "source_id": "1"}],
            portions=[{"food_key": "usda:1", "measure": "cup", "grams": 160.0,
                       "amount": amount, "method": "measured",
                       "confidence": 0.95, "source_id": "p1"}],
            nutrients=[],
        )

    assert "amount" in str(exc.value)


# --- a licence must never evaporate -------------------------------------

def test_equally_recent_snapshots_that_disagree_combine_their_licences(tmp_path):
    """MUTATION GUARD for `_source_for`.

    `max(matches, key=retrieved_at)` returns the FIRST maximal element, and
    `_sources` is ordered by `(provider, dataset_version)` — so a date tie
    resolved to the alphabetically-lowest version and could cite CC0-1.0 for
    data from an equally-recent ODbL snapshot, dropping the share-alike duty.
    An obligation must not vanish because a sibling input was more permissive.
    """
    path = tmp_path / "fc.sqlite"
    build_dataset(
        path,
        sources=[
            SourceRecord("off", "2024-a", "2024-01-01", "CC0-1.0"),
            SourceRecord("off", "2024-b", "2024-01-01", "ODbL-1.0"),
        ],
        foods=[{"key": "off:1", "name": "Branded flour", "provider": "off",
                "source_id": "1"}],
        portions=[{"food_key": "off:1", "measure": "cup", "grams": 120.0,
                   "method": "declared", "confidence": 0.7, "source_id": "p1"}],
        nutrients=[],
    )

    outcome = load_dataset(str(path)).portion_grams("off:1", "cup",
                                                    version=VERSION)

    assert outcome.provenance.license == "CC0-1.0 AND ODbL-1.0"


def test_a_newer_snapshot_still_wins_outright(tmp_path):
    # The conjunction above must not become a catch-all that conjoins every
    # snapshot a provider ever had — only the equally-recent ones.
    path = tmp_path / "fc.sqlite"
    build_dataset(
        path,
        sources=[
            SourceRecord("off", "2024-old", "2024-01-01", "CC0-1.0"),
            SourceRecord("off", "2026-new", "2026-01-01", "ODbL-1.0"),
        ],
        foods=[{"key": "off:1", "name": "Branded flour", "provider": "off",
                "source_id": "1"}],
        portions=[{"food_key": "off:1", "measure": "cup", "grams": 120.0,
                   "method": "declared", "confidence": 0.7, "source_id": "p1"}],
        nutrients=[],
    )

    prov = load_dataset(str(path)).portion_grams("off:1", "cup",
                                                 version=VERSION).provenance

    assert prov.license == "ODbL-1.0"
    assert prov.retrieved == "2026-01-01"


# --- every failure path is a DatasetError, not a bare ValueError --------

def test_an_unparseable_schema_version_is_a_typed_error(tmp_path):
    path = write_dataset(str(tmp_path / "fc.sqlite"))
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = 'two' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    # `int()` ran before any guard, so this raised a bare ValueError straight
    # past every host app's `except DatasetError`.
    with pytest.raises(DatasetError):
        load_dataset(path)


def test_a_dataset_naming_an_unknown_provider_is_a_typed_error(tmp_path):
    """The forward-compatibility case the schema check cannot catch.

    A structurally valid schema-2 file whose `source` row names a provider this
    foodcore has never heard of. `SourceRecord` enforces the closed vocabulary
    with ValueError, and `_read_sources` was not wrapped — so a host app
    crashed instead of seeing "upgrade foodcore".
    """
    path = write_dataset(str(tmp_path / "fc.sqlite"))
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("UPDATE source SET provider = 'fdc_future'")
    conn.commit()
    conn.close()

    with pytest.raises(DatasetError) as exc:
        load_dataset(path)
    assert "upgrade foodcore" in str(exc.value).lower()
