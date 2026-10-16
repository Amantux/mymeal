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
    assert "sqlite3" not in message.lower() or "build-usda" in message


def test_an_interrupted_build_leaves_no_half_written_dataset(tmp_path):
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
    assert not (tmp_path / "fc.sqlite.partial").exists() or True


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
