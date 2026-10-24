"""The foodcore seam: what happens with a dataset, and without one.

The load-bearing assertion in this file is the one about ABSENCE: with no
dataset configured (the shipped default) every number ``to_grams`` produces
must be the number it produced before foodcore existed. The rest of the file is
about not guessing — a refusal from the reference data falls back to myMeal's
own table, and an ambiguous food resolves to nothing at all.

The datasets here are built with foodcore's public ``build_dataset`` so the
values are fixtures, not a USDA import: the real export is gigabytes and is
never committed (see docs/configuration.md). The FDC ids below are plausible
shapes, not asserted facts about USDA's catalogue.
"""
from __future__ import annotations

import os

import pytest

# A hard import, NOT importorskip. foodcore is a shipped runtime dependency
# installed by one line in the Dockerfile and one in CI; if either breaks, this
# whole file skipping would turn a missing dependency into a green run with no
# coverage of the conversion path at all.
import foodcore

from app.services import food_reference, units


class _Cfg:
    """Minimal stand-in for the resolved settings object: food_reference reads
    it through getattr and needs exactly two names."""

    def __init__(self, data_dir, dataset="", enabled=True):
        self.DATA_DIR = str(data_dir)
        self.FOODCORE_DATASET = dataset
        self.FOODCORE_ENABLED = enabled


def _cfg(data_dir, dataset="", enabled=True):
    return _Cfg(data_dir, dataset, enabled)


class Cfg:
    """The two settings this seam reads, in the shape ``settings_access``
    hands over. A stub rather than ``load_settings`` so a test cannot be
    affected by a stray MYMEAL_* in the environment."""

    def __init__(self, dataset="", enabled=True):
        self.FOODCORE_DATASET = dataset
        self.FOODCORE_ENABLED = enabled


@pytest.fixture(autouse=True)
def _no_dataset_by_default(monkeypatch):
    """Default every test to the shipped configuration: no dataset.

    Also clears the adapter's caches on the way in AND the way out, so a test
    that opens a dataset cannot leak a handle into the next one.
    """
    food_reference.reset_cache()
    monkeypatch.setattr(food_reference, "dataset_path", lambda settings=None: "")
    yield
    food_reference.reset_cache()


def _dataset(tmp_path, monkeypatch, *, foods, portions, name="ref.sqlite"):
    path = tmp_path / name
    foodcore.build_dataset(
        path,
        sources=[foodcore.SourceRecord("usda", "April 2025", "2025-04-01", "CC0-1.0",
                                       len(foods))],
        foods=foods,
        portions=portions,
        nutrients=[],
    )
    monkeypatch.setattr(food_reference, "dataset_path", lambda settings=None: str(path))
    food_reference.reset_cache()
    return path


def _food(key, name):
    return {"key": key, "name": name, "provider": "usda", "source_id": key.split(":")[-1]}


def _portion(key, measure, grams):
    return {"food_key": key, "measure": measure, "grams": grams,
            "method": "measured", "confidence": 0.9, "source_id": f"{key}#{measure}"}


# --------------------------------------------------------------- no dataset

def test_with_no_dataset_to_grams_is_unchanged():
    """The non-negotiable one. These are the values myMeal shipped before
    foodcore existed: 236.588 ml/cup x the hand-written density, and None where
    the table has no entry. If foodcore ever influences this, it is a
    regression regardless of how good its number is."""
    assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53)
    assert units.to_grams("2 tbsp olive oil") == pytest.approx(14.7868 * 2 * 0.92)
    assert units.to_grams("200 g sugar") == pytest.approx(200)
    assert units.to_grams("2 cups diced onion") is None
    assert units.to_grams("3 eggs") is None


def test_no_dataset_means_no_file_is_ever_opened(monkeypatch):
    """Not merely "returns None": no dataset is opened and no food is resolved.
    A per-line dataset probe on a render path would be a performance bug even
    when it answers nothing."""
    monkeypatch.setattr(foodcore, "load_dataset",
                        lambda *a, **kw: pytest.fail("dataset opened"))
    monkeypatch.setattr(food_reference, "_resolve",
                        lambda *a, **kw: pytest.fail("food resolved"))
    assert food_reference.grams_per("cup", "flour") is None
    assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53)


def test_the_kill_switch_stands_the_seam_down(monkeypatch, tmp_path):
    """Only FOODCORE_ENABLED=false does this now.

    A blank path used to mean "no dataset"; since the library ships one, blank
    means "use what we ship" and the explicit off switch is the single way to
    get the built-in density table back. The old test asserted both, so this
    one was split rather than loosened — the kill switch is the part that must
    keep working exactly.
    """
    monkeypatch.undo()  # drop the autouse dataset_path stub; exercise the real one

    assert food_reference.dataset_path(Cfg(dataset=str(tmp_path / "x.sqlite"),
                                           enabled=False)) == ""
    assert food_reference.dataset_path(Cfg(dataset="", enabled=False)) == ""


def test_an_explicit_path_is_used_verbatim(monkeypatch, tmp_path):
    monkeypatch.undo()

    assert food_reference.dataset_path(
        Cfg(dataset=str(tmp_path / "x.sqlite"))) == str(tmp_path / "x.sqlite")


def test_a_blank_path_falls_through_to_the_shipped_dataset(monkeypatch,
                                                           isolated_expand,
                                                           tmp_path):
    """The behaviour change, asserted head-on: blank is no longer 'nothing'."""
    monkeypatch.undo()

    for blank in ("", "   "):
        got = food_reference.dataset_path(Cfg(dataset=blank))
        assert got.endswith(".sqlite"), f"{blank!r} gave {got!r}"
        assert os.path.isfile(got)


def _app_with(tmp_path, **settings):
    from app import create_app
    from app.config import Config

    class Cfg_(Config):
        DATA_DIR = str(tmp_path)
        DATABASE_URL = f"sqlite:///{tmp_path}/boot.db"
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"
        WORKER_ENABLED = False

    for key, value in settings.items():
        setattr(Cfg_, key, value)
    return create_app(Cfg_)


def test_the_app_boots_and_uses_the_shipped_dataset_by_default(tmp_path,
                                                               monkeypatch):
    """The whole settings chain, not a stub.

    This test previously asserted the OPPOSITE — that an unconfigured app used
    no dataset at all — and the change is the point of the feature. Reference
    data that only works for an operator who built a file by hand is a correct
    implementation that delivers nothing, so foodcore now ships a frozen CC0
    release and an unconfigured install uses it.

    What has NOT changed, and is asserted here: FOODCORE_DATASET stays blank
    (the shipped file is a default, not a value written into config), startup
    reports no foodcore warnings, and a food the dataset cannot resolve still
    falls through to the built-in density table rather than becoming an error.
    """
    monkeypatch.undo()  # exercise the real dataset_path / settings lookup
    app = _app_with(tmp_path)
    assert app.config["SETTINGS"].values["FOODCORE_DATASET"] == ""
    assert not [w for w in app.config["SETTINGS"].warnings if "FOODCORE" in w]
    with app.app_context():
        assert food_reference.dataset_path().endswith(".sqlite")
        # Flour is in the dataset; the value is USDA's measured 125 g/cup
        # rather than the hand table's density estimate.
        assert units.to_grams("1 cup flour") == pytest.approx(125.0, rel=0.02)
        # A food with no portion row still degrades, it does not raise.
        assert units.to_grams("2 cups diced onion") is None


def test_the_hand_table_still_answers_when_foodcore_is_turned_off(tmp_path,
                                                                  monkeypatch):
    """The off switch has to keep meaning what it meant. This is the old
    default, now reachable deliberately rather than by accident."""
    monkeypatch.undo()
    app = _app_with(tmp_path, FOODCORE_ENABLED=False)
    with app.app_context():
        assert food_reference.dataset_path() == ""
        assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53)


def test_a_configured_dataset_reaches_to_grams_through_real_settings(tmp_path, monkeypatch):
    monkeypatch.undo()
    path = tmp_path / "ref.sqlite"
    foodcore.build_dataset(
        path,
        sources=[foodcore.SourceRecord("usda", "April 2025", "2025-04-01", "CC0-1.0", 1)],
        foods=[_food("usda:168894", "Flour, wheat, all-purpose")],
        portions=[_portion("usda:168894", "cup", 125.0)],
        nutrients=[],
    )
    food_reference.reset_cache()
    app = _app_with(tmp_path, FOODCORE_DATASET=str(path))
    with app.app_context():
        assert food_reference.dataset_path() == str(path)
        assert units.to_grams("1 cup flour") == pytest.approx(125.0)


def test_a_missing_dataset_warns_but_never_blocks_startup(tmp_path, monkeypatch):
    """An enrichment layer must not be able to take the app down: a path typo
    is a warning on an app that started, not a refusal to start."""
    monkeypatch.undo()
    app = _app_with(tmp_path, FOODCORE_DATASET=str(tmp_path / "absent.sqlite"))
    assert any("FOODCORE_DATASET" in w for w in app.config["SETTINGS"].warnings)
    with app.app_context():
        assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53)


def test_a_missing_or_corrupt_dataset_file_degrades_to_the_table(monkeypatch, tmp_path):
    """An operator typo in a path, or a truncated file, must cost the reference
    data — not the request."""
    missing = tmp_path / "nope.sqlite"
    monkeypatch.setattr(food_reference, "dataset_path", lambda settings=None: str(missing))
    assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53)

    corrupt = tmp_path / "corrupt.sqlite"
    corrupt.write_bytes(b"this is not a database")
    food_reference.reset_cache()
    monkeypatch.setattr(food_reference, "dataset_path", lambda settings=None: str(corrupt))
    assert food_reference.grams_per("cup", "flour") is None
    assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53)


def test_foodcore_not_installed_degrades_to_the_table(monkeypatch, tmp_path):
    """The library is a path dependency; a deployment that skipped installing it
    must still convert."""
    path = _dataset(tmp_path, monkeypatch,
                    foods=[_food("usda:168894", "Flour, wheat, all-purpose")],
                    portions=[_portion("usda:168894", "cup", 125.0)])
    assert path.exists()
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def no_foodcore(name, *args, **kwargs):
        if name == "foodcore" or name.startswith("foodcore."):
            raise ImportError("No module named 'foodcore'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setitem(
        __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__,
        "__import__", no_foodcore)
    assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53)


# --------------------------------------------------------------- with a dataset

def test_a_measured_portion_weight_beats_the_hand_written_density(tmp_path, monkeypatch):
    """Stage 1's whole point. USDA publishes 125 g for a cup of all-purpose
    flour; the hand table says 236.588 x 0.53 = 125.4. Close here, but the
    measured value is the one that carries provenance, so it wins."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:168894", "Flour, wheat, all-purpose, enriched, bleached")],
             portions=[_portion("usda:168894", "cup", 125.0)])
    assert units.to_grams("1 cup flour") == pytest.approx(125.0)
    assert units.to_grams("2 cups flour") == pytest.approx(250.0)
    # ...and it reaches foods the 41-entry table never covered.
    _dataset(tmp_path, monkeypatch, name="quinoa.sqlite",
             foods=[_food("usda:168917", "Quinoa, uncooked")],
             portions=[_portion("usda:168917", "cup", 170.0)])
    assert units.to_grams("1 cup quinoa") == pytest.approx(170.0)


def test_mass_units_never_route_through_the_dataset(tmp_path, monkeypatch):
    """g/oz/lb are definitional. A dataset row for them must not be able to
    move a number units.py already gets exactly right."""
    # The invariant, asserted directly rather than only through to_grams, which
    # returns early for weight units and would pass with no guard here at all.
    assert not set(food_reference._MEASURES) & set(units._WEIGHT_G)
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "cheese")],
             portions=[_portion("usda:1", "oz", 999.0),
                       _portion("usda:1", "ounce", 999.0)])
    assert food_reference.grams_per("oz", "cheese") is None
    assert units.to_grams("2 ounces cheese") == pytest.approx(56.699)
    assert units.to_grams("1 kg cheese") == pytest.approx(1000.0)


def test_every_volume_unit_units_py_knows_can_reach_the_dataset(tmp_path, monkeypatch):
    """_MEASURES has to mirror units._VOLUME_ML. Adding a volume unit there and
    forgetting it here would silently make the reference path never fire for
    it — a gap nobody would notice, because the fallback still answers."""
    assert set(food_reference._MEASURES) == set(units._VOLUME_ML)


def test_unit_spellings_the_importer_may_have_used(tmp_path, monkeypatch):
    """The measure vocabulary is foodcore's internal normalisation, not a
    documented contract, so both plausible spellings are asked for."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "honey"), _food("usda:2", "molasses")],
             portions=[_portion("usda:1", "tablespoon", 21.0),
                       _portion("usda:2", "tbsp", 20.0)])
    assert units.to_grams("1 tbsp honey") == pytest.approx(21.0)
    assert units.to_grams("1 tbsp molasses") == pytest.approx(20.0)


# --------------------------------------------------------------- refusals

def test_no_portion_row_falls_back_to_the_density_table(tmp_path, monkeypatch):
    """THE FALLBACK TEST (mutation-checked: deleting the ``_density_for``
    branch in ``units.to_grams`` turns this red). The dataset knows the food
    but publishes no cup weight for it, so foodcore says Unresolved — and a
    refusal must not withdraw a conversion myMeal already shipped."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "flour")],
             portions=[_portion("usda:1", "gram", 1.0)])
    assert food_reference.grams_per("cup", "flour") is None
    assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53)


def test_an_unknown_food_falls_back_and_then_to_none(tmp_path, monkeypatch):
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "Flour, wheat, all-purpose")],
             portions=[_portion("usda:1", "cup", 125.0)])
    # In the dataset, not in the table.
    assert units.to_grams("1 cup flour") == pytest.approx(125.0)
    # In the table, not in the dataset.
    assert units.to_grams("1 cup honey") == pytest.approx(236.588 * 1.42)
    # In neither: still None, never a zero and never an exception.
    assert units.to_grams("2 cups diced onion") is None


def test_candidates_that_disagree_about_the_weight_refuse(tmp_path, monkeypatch):
    """The onion case, which is the honest hard part. USDA's cup weights for
    onion depend on the preparation — 160 g chopped, 115 g sliced, a 39%
    spread. Two equally-matched foods that disagree by that much are a choice,
    not a reading, so the conversion is refused and myMeal's own table (which
    has no onion) leaves the line unconverted exactly as it does today."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:170000", "Onions, raw"),
                    _food("usda:170001", "Onions, sliced, raw")],
             portions=[_portion("usda:170000", "cup", 160.0),
                       _portion("usda:170001", "cup", 115.0)])
    # Both score in the same (strong) tier — note the plural, which is how USDA
    # names them — so neither identity wins and their weights disagree by 39%.
    assert food_reference._score(food_reference._forms("onion"), "Onions, raw") == \
        food_reference._score(food_reference._forms("onion"), "Onions, sliced, raw")
    assert food_reference.grams_per("cup", "onion") is None
    assert units.to_grams("1 cup onions") is None
    assert units.to_weight_line("1 cup onions") == "1 cup onions"
    assert units.annotate_weight("1 cup onions") == "1 cup onions"


def test_a_single_unambiguous_candidate_resolves_despite_the_plural(tmp_path, monkeypatch):
    """The other half of the onion story: with ONE onion food there is nothing
    to be ambiguous about, and myMeal's singular canonical ("onion") must still
    reach USDA's plural description. This is the case the hand table never
    covered, so today it converts to nothing at all."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:170000", "Onions, raw")],
             portions=[_portion("usda:170000", "cup", 160.0)])
    assert units.to_grams("1 cup onions") == pytest.approx(160.0)
    assert units.to_grams("2 cups chopped onions") == pytest.approx(320.0)


def test_candidates_that_agree_resolve_to_the_most_generic(tmp_path, monkeypatch):
    """Ambiguity that cannot change the answer is not ambiguity. Three rows
    within 10% of each other are interchangeable for a cook, so the shortest
    (least-qualified) description wins rather than refusing usefully-known
    weights."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "Rice, white, long-grain, raw, enriched"),
                    _food("usda:2", "Rice, white, long-grain, raw"),
                    _food("usda:3", "Rice, white, short-grain, raw")],
             portions=[_portion("usda:1", "cup", 185.0),
                       _portion("usda:2", "cup", 185.0),
                       _portion("usda:3", "cup", 190.0)])
    assert units.to_grams("1 cup rice") == pytest.approx(185.0)


def test_weak_name_evidence_never_resolves(tmp_path, monkeypatch):
    """A name that merely CONTAINS the word is not evidence of being the food.
    "Onion rings, breaded" contains "onion"; serving its weight for "1 cup
    onions" is the class of silent guess this seam exists to stop."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "Onion rings, breaded, frozen, prepared")],
             portions=[_portion("usda:1", "cup", 80.0)])
    assert food_reference.grams_per("cup", "onion") is None
    assert units.to_grams("1 cup onions") is None


def test_the_material_boundary_rule_still_holds(tmp_path, monkeypatch):
    """Resolution runs on myMeal's canonical food name, so the existing
    "a nut butter is not butter" rule is not bypassed: "peanut butter"
    canonicalises to itself and cannot pick up butter's dataset row."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "Butter, salted")],
             portions=[_portion("usda:1", "cup", 227.0)])
    assert units.to_grams("1 cup butter") == pytest.approx(227.0)
    # Peanut butter keeps its own hand-written density (1.09), not butter's row.
    assert units.to_grams("1 cup peanut butter") == pytest.approx(236.588 * 1.09)


def test_learned_conversions_are_still_consulted_last(tmp_path, monkeypatch):
    """A remembered conversion for a unit nobody has physics for ("2 sticks
    butter") still works, and the reference dataset does not pre-empt it."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "Butter, salted")],
             portions=[_portion("usda:1", "cup", 227.0)])

    def lookup(unit, food):
        return 113.0 if unit == "stick" else None

    assert units.to_grams("2 sticks butter", learned=lookup) == pytest.approx(226.0)


def test_a_zero_weight_row_is_not_an_answer(tmp_path, monkeypatch):
    """Zero grams per cup is not a fact about a food, it is a broken row.
    Treating it as an answer would print "0 g" — the exact unknown-as-zero
    confusion foodcore exists to prevent."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "flour")],
             portions=[_portion("usda:1", "cup", 0.0)])
    assert food_reference.grams_per("cup", "flour") is None
    assert units.to_grams("1 cup flour") == pytest.approx(236.588 * 0.53)


def test_a_zero_row_beside_a_real_one_does_not_sneak_through(tmp_path, monkeypatch):
    """The distinguishing case: a tier where one row is broken and one is real.
    Averaging, or skipping the zero and trusting the rest, would both serve a
    number chosen by a data defect."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "Onions, raw"),
                    _food("usda:2", "Onions, young")],
             portions=[_portion("usda:1", "cup", 0.0),
                       _portion("usda:2", "cup", 160.0)])
    assert food_reference.grams_per("cup", "onion") is None


# --------------------------------------------- the wrong-preparation attacks

def test_a_qualified_sibling_cannot_answer_for_the_generic_food(tmp_path, monkeypatch):
    """Found in adversarial review. "Milk, whole" publishes no cup row, so the
    only row left in the winning tier was "Milk, dry, whole" at 128 g — against
    the 243.7 g myMeal has always printed. A tier where only SOME foods publish
    a weight is being decided by the portion table's gaps, not by evidence."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "Milk, dry, whole"),
                    _food("usda:2", "Milk, whole, 3.25% milkfat")],
             portions=[_portion("usda:1", "cup", 128.0),
                       _portion("usda:2", "fluid ounce", 30.5)])
    assert food_reference.grams_per("cup", "milk") is None
    assert units.to_grams("1 cup milk") == pytest.approx(236.588 * 1.03)


def test_wrong_preparations_cannot_endorse_each_other(tmp_path, monkeypatch):
    """Also found in adversarial review, and the nastier half: two DRIED onion
    rows agree with each other to within 5%, so a value-agreement check alone
    ratifies 46 g for "1 cup onions" — three and a half times wrong. A row may
    only answer for a preparation the line actually asked for."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "Onions, dried"),
                    _food("usda:2", "Onions, dehydrated flakes")],
             portions=[_portion("usda:1", "cup", 46.0),
                       _portion("usda:2", "cup", 48.0)])
    assert food_reference.grams_per("cup", "onion") is None
    assert units.to_grams("1 cup onions") is None


def test_asking_for_the_preparation_does_use_it(tmp_path, monkeypatch):
    """The guard is symmetric, which makes it a feature rather than only a
    refusal: a line that says "dried" may have the dried row. myMeal's own
    canonicalisation drops that word, so this is strictly new capability."""
    _dataset(tmp_path, monkeypatch,
             foods=[_food("usda:1", "Onions, dried")],
             portions=[_portion("usda:1", "cup", 46.0)])
    assert units.to_grams("1 cup dried onions") == pytest.approx(46.0)
    assert units.to_grams("1 cup onions") is None


def test_the_generic_row_is_found_past_a_crowd_of_short_names(tmp_path, monkeypatch):
    """search_foods returns the SHORTEST names first, so on a real FDC import
    eight short near-misses ("Rice cake", "Rice bran") crowded out the generic
    row and the dataset was loaded and useless. Asking for "<food>," as well
    targets USDA's comma-after-the-head-noun grammar."""
    distractors = ["Rice cake", "Rice bran", "Rice milk", "Rice wine",
                   "Rice chips", "Rice drink", "Rice flour", "Rice syrup",
                   "Rice pudding", "Rice crackers"]
    foods = [_food(f"usda:{i}", name) for i, name in enumerate(distractors, start=10)]
    foods.append(_food("usda:1", "Rice, white, long-grain, raw, enriched"))
    assert len(distractors) > food_reference._CANDIDATE_LIMIT
    _dataset(tmp_path, monkeypatch, foods=foods,
             portions=[_portion("usda:1", "cup", 185.0)])
    assert units.to_grams("1 cup rice") == pytest.approx(185.0)


# --- the shipped dataset: working by default ------------------------------

@pytest.fixture
def isolated_expand(tmp_path, monkeypatch):
    """Point BOTH expansion candidates at tmp_path.

    The real order is system-temp then DATA_DIR, and the system temp dir
    survives between tests — so without this a dataset expanded by an earlier
    test is found by a later one and the failure-mode tests silently assert
    nothing. (It cost three confusing failures to notice.)
    """
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    return tmp_path


def test_the_shipped_dataset_is_used_with_no_configuration(isolated_expand,
                                                           tmp_path,
                                                           monkeypatch):
    """The feature's whole value rested on an operator building a file by hand,
    which meant a correct implementation delivered nothing to anybody. foodcore
    ships a frozen CC0 USDA release; with no configuration at all it must be
    what gets used."""
    from app.services import food_reference as fr

    path = fr.shipped_dataset_path(_cfg(tmp_path))

    assert path, "the shipped dataset did not resolve"
    assert os.path.isfile(path)
    assert os.path.getsize(path) > 1_000_000       # expanded, not the gzip


def test_expanding_the_shipped_dataset_is_done_once(isolated_expand, tmp_path):
    """It is 10 MB expanded. Doing that on every call would turn a render path
    into a disk copy."""
    from app.services import food_reference as fr

    first = fr.shipped_dataset_path(_cfg(tmp_path))
    stamp = os.path.getmtime(first)
    second = fr.shipped_dataset_path(_cfg(tmp_path))

    assert first == second
    assert os.path.getmtime(second) == stamp      # not rewritten


def test_a_half_written_dataset_is_never_left_behind(isolated_expand, tmp_path, monkeypatch):
    """MUTATION GUARD. A container killed mid-expand must not leave a truncated
    SQLite file for the next boot to open as though it were sound."""
    from app.services import food_reference as fr

    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("shutil.copyfileobj", _boom)
    got = fr.shipped_dataset_path(_cfg(tmp_path))

    assert got == ""                               # degraded, did not raise
    leftovers = [p for p in os.listdir(tmp_path) if "partial" in p]
    assert leftovers == [], f"left a partial file: {leftovers}"


def test_an_unwritable_data_dir_degrades_instead_of_crashing(isolated_expand,
                                                             tmp_path,
                                                             monkeypatch):
    """A read-only or root-owned data dir is a real add-on failure mode. It must
    cost the conversion refinement, not the app."""
    from app.services import food_reference as fr

    monkeypatch.setattr("gzip.open", lambda *a, **kw: (_ for _ in ()).throw(
        OSError("permission denied")))

    assert fr.shipped_dataset_path(_cfg(tmp_path)) == ""


def test_an_explicit_dataset_still_wins_over_the_shipped_one(tmp_path):
    """An operator who built their own — a newer release, or Foundation Foods
    on top — must not be silently overridden by the default."""
    from app.services import food_reference as fr

    mine = tmp_path / "mine.sqlite"
    mine.write_bytes(b"x")

    assert fr._path_from(_cfg(tmp_path, dataset=str(mine))) == str(mine)


def test_turning_foodcore_off_disables_the_shipped_dataset_too(tmp_path):
    """FOODCORE_ENABLED=false means the built-in density table, full stop —
    otherwise the off switch would quietly stop working the moment a dataset
    started shipping."""
    from app.services import food_reference as fr

    assert fr._path_from(_cfg(tmp_path, enabled=False)) == ""


# --- the expansion directory is ours alone ---------------------------------

def test_a_hostile_symlink_is_never_opened_as_the_dataset(isolated_expand,
                                                          tmp_path):
    """SECURITY REGRESSION, proven exploitable before this guard existed.

    The dataset used to expand to a FIXED name directly in the system temp dir.
    Any other local user could plant a symlink there, and `os.path.isfile`
    follows symlinks, so the app opened whatever it pointed at as its reference
    data. Measured against the real code: a substituted dataset made
    "1 cup quinoa" answer 1700 g instead of 170 g — and the value still arrived
    stamped usda / CC0-1.0 / measured, laundered through the provenance that is
    supposed to make it trustworthy.

    Not exploitable inside the HA add-on, where /tmp belongs to the container,
    but a standalone or compose install shares the host's.
    """
    import os

    from app.services import food_reference as fr

    hostile = tmp_path / "attacker.sqlite"
    hostile.write_bytes(b"not a dataset")

    private = fr._private_dir(str(tmp_path))
    assert private, "the private directory should have been created"
    # Name derives from the asset's content hash now, so the test has to ask
    # for the same name the code will use rather than hardcode a release.
    import foodcore
    source = os.path.join(os.path.dirname(os.path.abspath(foodcore.__file__)),
                          "data", "usda-sr-legacy-2018-04.sqlite.gz")
    planted = os.path.join(
        private, f"{fr._SHIPPED_STEM}-{fr._asset_digest(source)}.sqlite")
    os.symlink(hostile, planted)

    got = fr.shipped_dataset_path(_cfg(tmp_path))

    # The symlink is removed and a real expansion put in its place — never
    # followed, and never simply trusted because something exists there.
    assert got == planted
    assert not os.path.islink(planted)
    assert os.path.getsize(planted) > 1_000_000


def test_a_shared_expansion_directory_is_refused_not_taken_over(tmp_path):
    """MUTATION GUARD. A directory at our path that anyone else can write to is
    not something to silently adopt — refusing costs the conversion refinement,
    which is the behaviour that preceded the shipped dataset."""
    import os

    from app.services import food_reference as fr

    shared = tmp_path / f"{fr._EXPANSION_DIR}-{os.geteuid()}"
    shared.mkdir(mode=0o777)
    os.chmod(shared, 0o777)          # mkdir honours umask; force it

    assert fr._private_dir(str(tmp_path)) == ""


def test_a_corrupt_asset_leaves_no_partial_behind(isolated_expand, tmp_path,
                                                  monkeypatch):
    """A truncated gzip raises EOFError or zlib.error — NEITHER is an OSError.
    Catching only OSError made the 'never raises' docstring false and left the
    cleanup unreachable, so every boot leaked a ~10 MB partial."""
    import gzip
    import os

    from app.services import food_reference as fr

    def _truncated(*a, **kw):
        raise EOFError("Compressed file ended before the end-of-stream marker")

    monkeypatch.setattr(gzip, "open", _truncated)

    got = fr.shipped_dataset_path(_cfg(tmp_path))

    assert got == ""                                   # degraded, did not raise
    leftovers = []
    for root, _dirs, files in os.walk(tmp_path):
        leftovers += [f for f in files if "partial" in f]
    assert leftovers == [], f"leaked a partial: {leftovers}"
