"""The build/inspect CLI, driven the way an operator drives it.

The USDA fixtures below are shaped like a FoodData Central CSV export but hold
invented ids and weights — they exercise the importer's structure, not USDA's
numbers, and nothing from them is ever shipped.
"""
import pytest

from foodcore import load_dataset, rule_version
from foodcore.cli import main
from foodcore.usda_import import UsdaImportError, collect, normalize_measure

VERSION = rule_version("convert")


def write_fdc_export(dirpath):
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "food.csv").write_text(
        "fdc_id,data_type,description\n"
        "1001,foundation_food,\"Flour, wheat, white\"\n"
        "1002,branded_food,\"Tomatoes, canned\"\n"
    )
    (dirpath / "measure_unit.csv").write_text("id,name\n9,cups\n10,undetermined\n")
    (dirpath / "food_portion.csv").write_text(
        "fdc_id,amount,measure_unit_id,portion_description,modifier,gram_weight\n"
        "1001,1,9,,,125\n"
        "1001,2,9,,,250\n"            # 2 cups = 250 g -> 125 g per cup
        "1002,1,10,,,400\n"           # unnameable measure -> skipped
    )
    (dirpath / "nutrient.csv").write_text(
        "id,name,unit_name\n"
        "1008,Energy,KCAL\n"
        "1003,Protein,G\n"
        "9999,Fluoride,UG\n"          # unmapped -> reported, not dropped silently
    )
    (dirpath / "food_nutrient.csv").write_text(
        "fdc_id,nutrient_id,amount\n"
        "1001,1008,364\n"
        "1001,1003,10.3\n"
        "1001,9999,1.1\n"
        "1002,1008,\n"                # no amount -> omitted, so lookups say unknown
    )
    return dirpath


# --- the importer -------------------------------------------------------

def test_collect_reads_foods_portions_and_mapped_nutrients(tmp_path):
    foods, portions, nutrients, stats = collect(str(write_fdc_export(tmp_path)))

    assert {f["key"] for f in foods} == {"usda:1001", "usda:1002"}
    assert {(p["measure"], p["grams"]) for p in portions} == {("cup", 125.0)}
    assert {(n["nutrient"], n["per_100g"]) for n in nutrients} == {
        ("energy", 364.0), ("protein", 10.3)
    }
    assert stats.skipped_portions == 1


def test_portion_grams_are_per_one_measure(tmp_path):
    _, portions, _, _ = collect(str(write_fdc_export(tmp_path)))

    # Both source rows describe the same cup; "2 cups = 250 g" must become
    # 125 g per cup, not 250.
    assert all(p["grams"] == 125.0 for p in portions)


def test_branded_values_are_declared_not_measured(tmp_path):
    write_fdc_export(tmp_path)
    (tmp_path / "food_portion.csv").write_text(
        "fdc_id,amount,measure_unit_id,portion_description,modifier,gram_weight\n"
        "1002,1,9,,,400\n"
    )

    _, portions, _, _ = collect(str(tmp_path))

    # A manufacturer's label is a weaker kind of evidence than a lab analysis,
    # and recording it as "measured" would erase that difference forever.
    assert [p["method"] for p in portions] == ["declared"]


def test_unmapped_nutrients_are_reported_not_silently_dropped(tmp_path):
    _, _, _, stats = collect(str(write_fdc_export(tmp_path)))

    assert "Fluoride" in stats.unmapped_nutrients
    assert "Fluoride" in stats.report()


def test_a_nutrient_with_no_amount_is_omitted_so_lookups_say_unknown(tmp_path):
    _, _, nutrients, _ = collect(str(write_fdc_export(tmp_path)))

    # Writing 0 here is the exact defect this package exists to prevent.
    assert not [n for n in nutrients if n["food_key"] == "usda:1002"]


def test_data_type_filter_limits_the_build(tmp_path):
    foods, _, _, _ = collect(str(write_fdc_export(tmp_path)),
                             data_types=("foundation_food",))

    assert [f["key"] for f in foods] == ["usda:1001"]


def test_a_directory_that_is_not_an_export_is_a_typed_error(tmp_path):
    with pytest.raises(UsdaImportError) as exc:
        collect(str(tmp_path))

    assert "food.csv" in str(exc.value)


def test_a_missing_directory_is_a_typed_error():
    with pytest.raises(UsdaImportError):
        collect("/nonexistent/fdc")


@pytest.mark.parametrize(
    ("unit", "description", "expected"),
    [
        ("cups", "", "cup"),
        ("tbsp", "", "tablespoon"),
        ("undetermined", "1 cup, chopped", "cup"),
        ("undetermined", "serving", None),
        ("", "", None),
    ],
)
def test_measure_normalisation(unit, description, expected):
    # Returning None rather than guessing "serving" is what lets an engine
    # refuse honestly instead of inventing a weight.
    assert normalize_measure(unit, description) == expected


# --- the CLI -----------------------------------------------------------

def test_build_usda_writes_a_loadable_dataset(tmp_path, capsys):
    export = write_fdc_export(tmp_path / "export")
    out = tmp_path / "fc.sqlite"

    code = main(["build-usda", "--fdc-dir", str(export), "--out", str(out),
                 "--dataset-version", "April 2025", "--retrieved", "2025-04-01"])

    assert code == 0
    assert "foods" in capsys.readouterr().out
    dataset = load_dataset(str(out))
    outcome = dataset.portion_grams("usda:1001", "cup", version=VERSION)
    assert outcome.value == 125.0
    assert outcome.provenance.license == "CC0-1.0"
    assert outcome.provenance.retrieved == "2025-04-01"


def test_build_usda_records_the_snapshot_in_the_registry(tmp_path):
    export = write_fdc_export(tmp_path / "export")
    out = tmp_path / "fc.sqlite"
    main(["build-usda", "--fdc-dir", str(export), "--out", str(out),
          "--dataset-version", "April 2025"])

    record = load_dataset(str(out)).sources[0]

    assert (record.provider, record.dataset_version) == ("usda", "April 2025")
    assert record.license == "CC0-1.0"


def test_build_usda_on_a_bad_directory_exits_nonzero_with_a_message(tmp_path, capsys):
    code = main(["build-usda", "--fdc-dir", str(tmp_path), "--out",
                 str(tmp_path / "x.sqlite"), "--dataset-version", "v1"])

    assert code == 1
    assert "foodcore:" in capsys.readouterr().err


def test_info_describes_the_bundled_fallback_as_minimal(monkeypatch, capsys):
    from foodcore.datasets import DATASET_ENV_VAR

    monkeypatch.delenv(DATASET_ENV_VAR, raising=False)

    assert main(["info"]) == 0
    out = capsys.readouterr().out
    assert "bundled minimal dataset" in out
    assert "NOTE:" in out


def test_info_on_a_built_dataset_lists_its_sources(tmp_path, capsys):
    export = write_fdc_export(tmp_path / "export")
    out = tmp_path / "fc.sqlite"
    main(["build-usda", "--fdc-dir", str(export), "--out", str(out),
          "--dataset-version", "April 2025"])

    main(["info", "--dataset", str(out)])

    printed = capsys.readouterr().out
    assert "usda April 2025" in printed
    assert "CC0-1.0" in printed


def test_info_on_a_missing_dataset_exits_nonzero(tmp_path, capsys):
    code = main(["info", "--dataset", str(tmp_path / "absent.sqlite")])

    assert code == 1
    assert "absent.sqlite" in capsys.readouterr().err
