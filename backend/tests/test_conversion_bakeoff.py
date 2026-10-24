"""The resolver bake-off: three strategies, 122 real recipe lines, one winner.

``docs/foodcore-consumer-architecture.md`` recommendation 2 says myMeal and
Edibl answer "which food is this?" with two different algorithms against the
same dataset, and that the duplication must be decided by measurement rather
than by opinion. This file is that measurement, kept in the repo so the
comparison is repeatable and so a future change to either resolver has to move
a number here deliberately instead of by accident.

The three strategies, each scored exactly where a user meets them — the whole
of ``units.to_grams``, density table included:

* **A** myMeal's own scorer (``_score``/``_forms``/``_MIN_SCORE``, the
  state-word filter and the 10% agreement gate) and then the hand-written
  ``units._DENSITY_G_PER_ML``. This is what shipped before this file existed.
* **B** foodcore's ``resolve_food`` plus its household vocabulary
  (``foodcore.rules.household_v1``) and ``foodcore.convert.convert``, then the
  density table. This is "delete myMeal's scorer".
* **C** B first, A second, density table last — the hybrid, and what
  ``food_reference.grams_per`` now does.

The corpus
----------
122 distinct (unit, food) lines, harvested rather than invented:

* ``repo`` — every quantity+volume-unit ingredient line literal in
  ``backend/tests`` and the frontend specs, deduplicated by (unit, food).
* ``owner-30`` — the ten lines from the brief that converted to nothing.
* ``household-vocab`` — every bare word ``foodcore.rules.household_v1``
  claims to know.
* ``hand-table`` — every food ``units._DENSITY_G_PER_ML`` claims to know.

The last two matter: taking both vocabularies in full is what keeps the corpus
from being cherry-picked for either side.

**And it is still blind in one direction**, which is the lesson of this file:
every line in it is a phrase one of the resolvers already knows, so it cannot
see what happens to an ordinary modifier in front of an ordinary food. That is
where the worst failure lived. ``MODIFIER_LINES``, further down, is the second
table built to cover it.

Expected values
---------------
``expected`` is grams for ONE unit, tagged with where the number came from:

* ``usda`` — the measured SR Legacy portion for the food a competent cook
  means. 100 lines.
* ``hand`` — no USDA row exists for that food at all, so myMeal's density
  table is the only source there has ever been. 5 lines.
* ``refuse`` — USDA publishes only materially-different qualified rows (a cup
  of onion is 160 g chopped and 115 g sliced), so **a refusal is the correct
  answer** and any number is an overclaim. 11 lines.
* ``unknown`` — not knowable from either source ("2 cups beans"). Counted for
  coverage, excluded from correctness. 6 lines.

A caveat stated rather than hidden: on the ``household-vocab`` lines the
expected value and strategy B's answer are both ultimately "the USDA row that
vocabulary cites", so B's *correctness* there is partly true by construction.
Its *coverage* is not, and neither is the gap between the expected value and
what the density table says — which is where most of the difference comes from.

The result
----------
=========  ========  ===========  =====  ==========
strategy   converts  correct      wrong  overclaims
=========  ========  ===========  =====  ==========
A          85/122    77/105           7        0/11
B          89/122    82/105           6        0/11
C          92/122    85/105           6        0/11
=========  ========  ===========  =====  ==========

C wins on both axes and ties A on the axis A was already perfect on, so C
ships. Three things that reading the table will not tell you:

* **Pure B is a coverage regression and was never an option.** Measured with
  no density table at all it converts 55/122 against A's 26 — but the table is
  what answers "1 cup broth", "1 cup cocoa", "1 cup almond butter" today, and
  the brief's hard constraint is that no line that converts correctly now may
  stop. So every strategy keeps it last, and "delete myMeal's scorer" means
  delete the *scorer*, not the table.
* **A's remaining value is a real tail, not sentiment.** Where the household
  vocabulary is silent and foodcore's matcher refuses, A still answers from
  the same data: quinoa 170 g, parsley 3.8 g/tbsp, vinegar 239 g. Dropping it
  (B, 82 correct) costs three correct conversions against C's 85.
* **This table was a ``BLOCK`` verdict before it was a result.** The first
  implementation scored 94/88 by also accepting foodcore's *lone weak*
  matches, and an adversarial review proved what that bought: "1 cup whole
  oats" answering 37 g from a breakfast-cereal brand where 97 g shipped.
  Killing that class (``_matched_food``'s floor) gave back one conversion and
  three correct answers; honouring the dataset's own ambiguity refusal
  (``_only_qualified``) gave back one more conversion and no correctness. A
  scoreboard is not a safety argument, which is why ``MODIFIER_LINES`` exists.

A's seven wrong answers are all the density table: oats 97 g against a
measured 81, cooked rice 201 g against 158, cream of tartar 213 g against 144.
C's six are five of those same table entries — cocoa, ketchup, powdered
sugar and cream of tartar, each a food foodcore has and cannot name from a
cook's word — plus one line this change introduces: ``1 tsp parmesan`` at
2.1 g where USDA's own tablespoon row implies 1.67 g, because foodcore derives
a density from the cup row instead of the nearer tablespoon one. That is a
library-side nuance, recorded in the PR.

The ten dead lines from the brief are still dead, and that is the finding:
breadcrumbs, lentils, chickpeas, polenta, almonds, walnuts, cream cheese,
tahini, cornmeal and shredded cheddar fail in *both* resolvers, because USDA
describes them as "Bread, crumbs, dry, grated, plain" and "Nuts, almonds" and
neither algorithm can get from a cook's word to those rows. Two of them
(almonds, walnuts) are qualified-only and *should* refuse. The other eight need
an entry in foodcore's household vocabulary, which is a different repository.
"""
from __future__ import annotations

import statistics

import pytest

# A hard import for the reason test_food_reference.py gives: foodcore is a
# shipped runtime dependency, and a skip here would turn a broken install into
# a green run with no coverage of the conversion path.
import foodcore

from app.services import food_reference, food_resolve, units

#: How far from the expected value still counts as right. Volume measures are
#: not repeatable to better than this — two cooks filling the same cup differ —
#: so a tighter tolerance would measure noise, and a looser one would stop
#: distinguishing a measured portion from a guessed density.
TOLERANCE = 0.10

#: foodcore's three evidence tiers, restated here as the claim they are. They
#: are not imported: ``datasets._SCORE_*`` is private, and copying a private
#: constant is how a library's internal renumbering becomes a silent policy
#: change in a consumer. Naming them and asserting them against live
#: ``resolve_food`` output (``test_the_two_score_scales_still_mean_the_same
#: _thing``) turns the assumption into a failing test instead.
_SCORE_EXACT = 100        #: the query IS the head noun
_SCORE_INSIDE_HEAD = 70   #: the query sits inside the head segment
_SCORE_ANYWHERE = 50      #: the query appears somewhere in the description

#: (unit, food, origin, expected grams per ONE unit, tag) — see the docstring.
CORPUS = (
    ("cup", "all-purpose flour", "repo", 125.0, "usda"),
    ("cup", "almond milk", "repo", 240.0, "usda"),
    ("cup", "beans", "repo", None, "unknown"),
    ("cup", "brown sugar", "repo", 220.0, "usda"),
    ("cup", "butter", "repo", 227.0, "usda"),
    ("cup", "chopped onions", "repo", 160.0, "usda"),
    ("cup", "cream", "repo", 238.0, "usda"),
    ("cup", "diced onion", "repo", 160.0, "usda"),
    ("cup", "dried onions", "repo", 56.0, "usda"),
    ("cup", "flour", "repo", 125.0, "usda"),
    ("cup", "granulated sugar", "repo", 200.0, "usda"),
    ("cup", "honey", "repo", 339.0, "usda"),
    ("cup", "milk", "repo", 244.0, "usda"),
    ("cup", "olive oil", "repo", 216.0, "usda"),
    ("cup", "onion", "repo", None, "refuse"),
    ("cup", "onions", "repo", None, "refuse"),
    ("cup", "peanut butter", "repo", 258.0, "usda"),
    ("cup", "plain flour", "repo", 125.0, "usda"),
    ("cup", "quinoa", "repo", 170.0, "usda"),
    ("cup", "rice", "repo", 185.0, "usda"),
    ("cup", "rice vinegar", "repo", 239.0, "hand"),
    ("cup", "sifted flour", "repo", 125.0, "usda"),
    ("cup", "soy sauce", "repo", 255.0, "usda"),
    ("cup", "stock", "repo", 240.0, "usda"),
    ("cup", "sugar", "repo", 200.0, "usda"),
    ("cup", "tomatoes", "repo", None, "refuse"),
    ("cup", "water", "repo", 236.6, "usda"),
    ("cup", "whole milk", "repo", 244.0, "usda"),
    ("ml", "coconut milk", "repo", 0.96, "usda"),
    ("ml", "milk", "repo", 1.031, "usda"),
    ("ml", "stock", "repo", 1.014, "usda"),
    ("ml", "water", "repo", 1.0, "usda"),
    ("tbsp", "butter", "repo", 14.2, "usda"),
    ("tbsp", "chili flakes", "repo", None, "unknown"),
    ("tbsp", "cornstarch", "repo", 8.0, "usda"),
    ("tbsp", "extra virgin olive oil", "repo", 13.5, "usda"),
    ("tbsp", "finely chopped parsley", "repo", 3.8, "usda"),
    ("tbsp", "honey", "repo", 21.0, "usda"),
    ("tbsp", "molasses", "repo", 21.1, "usda"),
    ("tbsp", "oil", "repo", 13.6, "usda"),
    ("tbsp", "olive oil", "repo", 13.5, "usda"),
    ("tbsp", "parsley", "repo", 3.8, "usda"),
    ("tbsp", "peanut butter", "repo", 16.0, "usda"),
    ("tbsp", "rice vinegar", "repo", 14.9, "hand"),
    ("tbsp", "soy sauce", "repo", 16.0, "usda"),
    ("tbsp", "sugar", "repo", 12.5, "usda"),
    ("tbsp", "vietnamese cinnamon", "repo", 7.8, "usda"),
    ("tsp", "chili flakes", "repo", None, "unknown"),
    ("tsp", "cinnamon", "repo", 2.6, "usda"),
    ("tsp", "fine salt", "repo", 6.0, "usda"),
    ("tsp", "garlic powder", "repo", 3.1, "usda"),
    ("tsp", "parmesan", "repo", 1.67, "usda"),
    ("tsp", "saigon cinnamon", "repo", 2.6, "usda"),
    ("tsp", "salt", "repo", 6.0, "usda"),
    ("tsp", "sesame oil", "repo", 4.5, "usda"),
    ("tsp", "vanilla extract", "repo", 4.2, "usda"),
    ("tsp", "oil", "repo", 4.5, "usda"),
    ("cup", "pitted dates", "repo", None, "unknown"),
    ("tbsp", "flour", "repo", 7.8, "usda"),
    ("cup", "breadcrumbs", "owner-30", 108.0, "usda"),
    ("cup", "lentils", "owner-30", 192.0, "usda"),
    ("cup", "chickpeas", "owner-30", 200.0, "usda"),
    ("cup", "polenta", "owner-30", None, "unknown"),
    ("cup", "almonds", "owner-30", None, "refuse"),
    ("cup", "walnuts", "owner-30", None, "refuse"),
    ("cup", "cream cheese", "owner-30", 232.0, "usda"),
    ("tbsp", "tahini", "owner-30", 15.0, "usda"),
    ("cup", "cornmeal", "owner-30", 157.0, "usda"),
    ("cup", "shredded cheddar", "owner-30", 113.0, "usda"),
    ("cup", "caster sugar", "household-vocab", 200.0, "usda"),
    ("cup", "cornstarch", "household-vocab", 128.0, "usda"),
    ("cup", "cornflour", "household-vocab", 128.0, "usda"),
    ("cup", "baking powder", "household-vocab", 220.8, "usda"),
    ("cup", "baking soda", "household-vocab", 220.8, "usda"),
    ("cup", "bicarbonate of soda", "household-vocab", 220.8, "usda"),
    ("cup", "vanilla extract", "household-vocab", 208.0, "usda"),
    ("cup", "oats", "household-vocab", 81.0, "usda"),
    ("cup", "heavy cream", "household-vocab", 238.0, "usda"),
    ("cup", "double cream", "household-vocab", 238.0, "usda"),
    ("cup", "sour cream", "household-vocab", 230.0, "usda"),
    ("cup", "yogurt", "household-vocab", 245.0, "usda"),
    ("cup", "yoghurt", "household-vocab", 245.0, "usda"),
    ("cup", "parmesan", "household-vocab", 100.0, "usda"),
    ("cup", "mozzarella", "household-vocab", None, "refuse"),
    ("cup", "cheddar", "household-vocab", None, "refuse"),
    ("cup", "egg", "household-vocab", 243.0, "usda"),
    ("cup", "eggs", "household-vocab", 243.0, "usda"),
    ("cup", "oil", "household-vocab", 218.0, "usda"),
    ("cup", "vegetable oil", "household-vocab", 218.0, "usda"),
    ("cup", "canola oil", "household-vocab", 218.0, "usda"),
    ("cup", "maple syrup", "household-vocab", 315.0, "usda"),
    ("cup", "lemon juice", "household-vocab", 244.0, "usda"),
    ("cup", "garlic", "household-vocab", 136.0, "usda"),
    ("cup", "carrots", "household-vocab", None, "refuse"),
    ("cup", "potatoes", "household-vocab", None, "refuse"),
    ("cup", "celery", "household-vocab", None, "refuse"),
    ("cup", "salt", "household-vocab", 292.0, "usda"),
    ("cup", "cooked rice", "household-vocab", 158.0, "usda"),
    ("cup", "cooked pasta", "household-vocab", None, "refuse"),
    ("cup", "broth", "hand-table", 240.0, "usda"),
    ("cup", "syrup", "hand-table", None, "unknown"),
    ("cup", "powdered sugar", "hand-table", 120.0, "usda"),
    ("cup", "cocoa", "hand-table", 86.0, "usda"),
    ("cup", "cocoa powder", "hand-table", 86.0, "usda"),
    ("cup", "ketchup", "hand-table", 240.0, "usda"),
    ("cup", "almond butter", "hand-table", 250.0, "usda"),
    ("cup", "cashew butter", "hand-table", 256.0, "usda"),
    ("cup", "condensed milk", "hand-table", 306.0, "usda"),
    ("cup", "evaporated milk", "hand-table", 252.0, "usda"),
    ("cup", "buttermilk", "hand-table", 245.0, "usda"),
    ("cup", "oat milk", "hand-table", 246.1, "hand"),
    ("cup", "soy milk", "hand-table", 243.0, "usda"),
    ("cup", "coconut milk", "hand-table", 226.0, "usda"),
    ("cup", "coconut cream", "hand-table", 240.0, "usda"),
    ("cup", "coconut oil", "hand-table", 218.0, "usda"),
    ("cup", "chicken stock", "hand-table", 240.0, "usda"),
    ("cup", "beef stock", "hand-table", 240.0, "usda"),
    ("cup", "vegetable stock", "hand-table", 220.0, "usda"),
    ("cup", "rice wine", "hand-table", 234.2, "hand"),
    ("cup", "vinegar", "hand-table", 239.0, "usda"),
    ("cup", "almond flour", "hand-table", 99.4, "hand"),
    ("cup", "cream of tartar", "hand-table", 144.0, "usda"),
)

#: The lines whose number this change moves, pinned before -> after so a future
#: resolver edit cannot quietly walk one of them back. Every row but three
#: moves toward the expected value; the exceptions are stated in the docstring.
CHANGED = (
    ("cup", "all-purpose flour", 125.39, 125.0),
    ("cup", "butter", 227.12, 227.0),
    ("cup", "cream", 230.0, 236.59),
    ("cup", "flour", 125.39, 125.0),
    ("cup", "granulated sugar", 201.1, 200.0),
    ("cup", "milk", 245.0, 244.0),
    ("cup", "olive oil", 217.66, 216.0),
    ("cup", "peanut butter", 257.88, 258.0),
    ("cup", "plain flour", 125.39, 125.0),
    ("cup", "rice", 201.1, 185.0),
    ("cup", "sifted flour", 125.39, 125.0),
    ("cup", "sugar", 201.1, 200.0),
    ("cup", "whole milk", 245.0, 244.0),
    ("tbsp", "cornstarch", 7.84, 8.0),
    ("tbsp", "extra virgin olive oil", 13.6, 13.5),
    ("tbsp", "molasses", None, 21.06),
    ("tbsp", "oil", 13.6, 13.5),
    ("tbsp", "olive oil", 13.6, 13.5),
    ("tbsp", "peanut butter", 16.12, 16.0),
    ("tbsp", "sugar", 12.57, 12.5),
    ("tsp", "parmesan", None, 2.08),
    ("tbsp", "flour", 7.84, 7.81),
    ("cup", "caster sugar", 201.1, 200.0),
    ("cup", "cornstarch", 125.39, 128.0),
    ("cup", "cornflour", None, 128.0),
    ("cup", "baking powder", None, 220.8),
    ("cup", "baking soda", None, 220.8),
    ("cup", "bicarbonate of soda", None, 220.8),
    ("cup", "oats", 97.0, 81.0),
    ("cup", "yogurt", 243.69, 245.0),
    ("cup", "yoghurt", 243.69, 245.0),
    ("cup", "parmesan", None, 100.0),
    ("cup", "oil", 218.0, 216.0),
    ("cup", "maple syrup", 324.13, 315.0),
    ("cup", "cooked rice", 201.1, 158.0),
)


@pytest.fixture(scope="module")
def shipped():
    """The real USDA dataset foodcore ships, which is also the production
    default (``FOODCORE_DATASET`` blank means "use the shipped one").

    Scored against the real 7,793-food catalogue on purpose: every gate in
    either resolver exists because a small fixture cannot tell an ambiguous
    food from an unambiguous one, so a bake-off on fixtures would measure
    nothing. Expanding the asset costs ~0.1 s and is cached by content hash.
    """
    path = food_reference.shipped_dataset_path()
    if not path:
        pytest.skip("this foodcore install ships no dataset asset")
    patch = pytest.MonkeyPatch()
    patch.setattr(food_reference, "dataset_path", lambda settings=None: path)
    food_reference.reset_cache()
    yield foodcore.load_dataset(path, allow_minimal=False)
    patch.undo()
    food_reference.reset_cache()


def _hand_table(unit, food):
    """What ``units.to_grams`` falls back to: the hand-written density."""
    density = units._density_for(food)
    if not density or unit not in units._VOLUME_ML:
        return None
    return density * units._VOLUME_ML[unit]


def _canonical(food):
    return (food_resolve.match_key(food) or "").strip().lower()


def _strategy_a(dataset, unit, food):
    """myMeal's scorer, then the density table — the pre-change behaviour."""
    canonical = _canonical(food)
    grams = _lookup_a(dataset, unit, food, canonical) if canonical else None
    return grams or _hand_table(unit, food)


def _lookup_a(dataset, unit, food, canonical):
    return food_reference._lookup_grams(
        dataset, canonical, food_reference._MEASURES[unit],
        food_reference._declared_states(food),
    )


def _strategy_b(dataset, unit, food):
    """foodcore's resolver and vocabulary, then the density table."""
    return _strategy_b_reference_only(dataset, unit, food) or _hand_table(unit, food)


def _strategy_c(_dataset, unit, food):
    """What ships: ``grams_per`` (foodcore, then myMeal) and then the table."""
    return food_reference.grams_per(unit, food) or _hand_table(unit, food)


def _score(dataset, strategy):
    """Coverage and correctness for one strategy over the whole corpus."""
    converts = correct = wrong = missing = overclaims = 0
    errors = []
    for unit, food, _origin, expected, tag in CORPUS:
        grams = strategy(dataset, unit, food)
        if grams:
            converts += 1
        if tag == "refuse":
            overclaims += 1 if grams else 0
            continue
        if tag == "unknown":
            continue
        if not grams:
            missing += 1
            continue
        error = abs(grams - expected) / expected
        errors.append(error)
        if error <= TOLERANCE:
            correct += 1
        else:
            wrong += 1
    return {
        "converts": converts, "correct": correct, "wrong": wrong,
        "missing": missing, "overclaims": overclaims,
        "median_error": statistics.median(errors) if errors else 0.0,
    }


def test_corpus_is_the_size_and_shape_the_docstring_claims():
    """The scoreboard's denominators are only meaningful if the corpus is."""
    assert len(CORPUS) == 122
    assert len({(unit, food) for unit, food, *_ in CORPUS}) == 122

    tags = [row[4] for row in CORPUS]
    assert tags.count("usda") == 100
    assert tags.count("hand") == 5
    assert tags.count("refuse") == 11
    assert tags.count("unknown") == 6


def test_the_dataset_answers_far_more_lines_than_before(shipped):
    """What each resolver is worth on its own, with the density table taken
    away: this is the number that says the dataset went from decoration to
    load-bearing, and the one that says the table cannot yet be retired."""
    def reference_only(strategy):
        return sum(1 for unit, food, *_ in CORPUS if strategy(shipped, unit, food))

    assert reference_only(_strategy_a_reference_only) == 26
    assert reference_only(_strategy_b_reference_only) == 55
    assert reference_only(_strategy_c_reference_only) == 59


def _strategy_a_reference_only(dataset, unit, food):
    canonical = _canonical(food)
    return _lookup_a(dataset, unit, food, canonical) if canonical else None


def _strategy_b_reference_only(dataset, unit, food):
    identified = food_reference._lookup_reference_food(
        dataset, food, _canonical(food), food_reference._declared_states(food),
    )
    if identified is None:
        return None
    return food_reference._reference_grams(dataset, identified, unit)


def _strategy_c_reference_only(_dataset, unit, food):
    return food_reference.grams_per(unit, food)


def test_strategy_a_scores_what_it_scored_before_the_change(shipped):
    """The baseline. A is still reachable — ``grams_per`` falls back to it —
    so a change that breaks it has to break this first."""
    assert _score(shipped, _strategy_a) == {
        "converts": 85, "correct": 77, "wrong": 7, "missing": 21,
        "overclaims": 0, "median_error": pytest.approx(0.0051, abs=0.0005),
    }


def test_strategy_b_beats_a_but_loses_to_the_hybrid(shipped):
    """Pure foodcore: +6 conversions and +8 correct over A, and the reason
    myMeal's scorer is no longer consulted first."""
    score = _score(shipped, _strategy_b)
    assert score["converts"] == 89
    assert score["correct"] == 82
    assert score["wrong"] == 6
    assert score["overclaims"] == 0


def test_strategy_c_wins_on_coverage_and_on_correctness(shipped):
    """The shipped strategy, and the whole justification for the change:
    +9 conversions and +11 correct against A, with three fewer wrong."""
    score = _score(shipped, _strategy_c)
    assert score["converts"] == 92
    assert score["correct"] == 85
    assert score["wrong"] == 6
    assert score["missing"] == 14
    # Zero, and that is the axis A was already perfect on: every one of the
    # eleven qualifier-ambiguous lines still refuses.
    assert score["overclaims"] == 0


def test_no_line_that_converts_correctly_today_regresses(shipped):
    """The brief's hard constraint, checked line by line rather than on the
    totals: a strategy can improve its score and still break one line."""
    regressions = []
    for unit, food, _origin, expected, tag in CORPUS:
        if tag in ("refuse", "unknown"):
            continue
        before = _strategy_a(shipped, unit, food)
        if not before or abs(before - expected) / expected > TOLERANCE:
            continue
        after = _strategy_c(shipped, unit, food)
        if not after or abs(after - expected) / expected > TOLERANCE:
            regressions.append((unit, food, before, after, expected))
    assert regressions == []


def test_a_refusal_never_becomes_an_overclaim(shipped):
    """The other half of "never regress", and the one myMeal was already
    perfect on: A refuses all eleven qualifier-ambiguous lines, so C must
    refuse all eleven too. A coverage win bought by answering a question the
    dataset says is ambiguous is not a win.

    This is what ``_only_qualified`` is for. Mutation-checked: make it return
    ``False`` and "1 cup celery" answers 120 g against a measured 101 g."""
    before = {(unit, food) for unit, food, _o, _e, tag in CORPUS
              if tag == "refuse" and _strategy_a(shipped, unit, food)}
    after = {(unit, food) for unit, food, _o, _e, tag in CORPUS
             if tag == "refuse" and _strategy_c(shipped, unit, food)}
    assert before == set()
    assert after == set()


@pytest.mark.parametrize(("unit", "food", "before", "after"), CHANGED)
def test_the_lines_this_change_moves_are_pinned(shipped, unit, food, before, after):
    """Every number that moved, nailed down. This is the test that fails when
    someone reorders the resolvers or bumps foodcore's vocabulary."""
    assert _strategy_a(shipped, unit, food) == pytest.approx(before, abs=0.01)
    assert _strategy_c(shipped, unit, food) == pytest.approx(after, abs=0.01)


def test_the_brief_ten_are_still_dead_and_the_gap_is_the_vocabulary(shipped):
    """The negative result, pinned so it changes visibly when foodcore's
    household vocabulary grows the eight words it is missing."""
    dead = [("cup", "breadcrumbs"), ("cup", "lentils"), ("cup", "chickpeas"),
            ("cup", "polenta"), ("cup", "almonds"), ("cup", "walnuts"),
            ("cup", "cream cheese"), ("tbsp", "tahini"), ("cup", "cornmeal"),
            ("cup", "shredded cheddar")]
    for unit, food in dead:
        assert _strategy_a(shipped, unit, food) is None, food
        assert _strategy_c(shipped, unit, food) is None, food

    # Not for want of data: the rows exist and publish exactly the weight the
    # line wants. What is missing is the step from the cook's word to the row.
    version = foodcore.rule_version("convert")
    for description, grams in (("Bread, crumbs, dry, grated, plain", 108.0),
                               ("Lentils, raw", 192.0),
                               ("Cheese, cream", 232.0)):
        rows = shipped.search_foods(description, 1)
        assert rows, description
        outcome = shipped.portion_grams(rows[0].key, "cup", version=version)
        assert outcome.resolved and outcome.value == grams, description


def test_the_household_vocabulary_is_consulted_only_after_the_matcher(shipped):
    """A convention must never override an explicit food. "flour" alone is
    ambiguous as data and unambiguous as language, so it takes wheat flour's
    125 g; "arrowroot flour" names its own food and keeps it."""
    assert food_reference.grams_per("cup", "flour") == 125.0
    assert food_reference.grams_per("cup", "arrowroot flour") == 128.0


def test_canonical_fallback_refuses_when_a_state_word_was_stripped(shipped):
    """The guard in ``_reference_food``, and the reason it exists.

    ``match_key`` drops "dried" — correct for identity, wrong for mass — so
    without the guard "1 cup dried celery" is answered by the "celery"
    convention with RAW celery's weight, which is the error the state-word
    list exists to prevent. Mutation-checked: replacing ``stripped`` with
    ``False`` in ``_reference_food`` makes this assertion fail with 120.0.
    """
    assert food_reference.grams_per("cup", "dried celery") is None

    # And the guard is not simply "refuse anything with a state word": the
    # vocabulary's own state-bearing keys still resolve, from the text as
    # written, with the cooked weight rather than the raw one.
    assert food_reference.grams_per("cup", "cooked rice") == 158.0
    assert food_reference.grams_per("cup", "rice") == 185.0


def test_a_confident_match_can_be_absurd_and_is_pinned_here(shipped):
    """A foodcore weakness, recorded rather than worked around.

    ``resolve_food`` is confident whenever one food uniquely contains every
    query token — and for "dried onions" that food is a bag of sour-cream and
    onion crisps, whose name happens to contain "dried" (of the potatoes) and
    "onion" (of the flavour). It scores 50, "the tokens appear somewhere",
    which is the evidence floor in ``_matched_food``.

    Both halves are asserted: that foodcore still says yes, and that this app
    still says no. The day foodcore tightens its own rule, the first half
    fails and says so out loud.
    """
    match = shipped.resolve_food("dried onions")
    assert match.confident
    assert "potato chips" in match.food.description
    assert match.candidates[0][1] < food_reference._MIN_SCORE

    assert food_reference._matched_food(shipped, "dried onions") is None
    assert food_reference.grams_per("cup", "dried onions") is None


def test_a_qualifier_ambiguous_food_still_refuses(shipped):
    """myMeal's rule that ambiguity refuses survives the new resolver: USDA
    knows a cup of onion only as chopped (160 g) or sliced (115 g), a 39%
    spread, and no number is the honest answer."""
    assert food_reference.grams_per("cup", "onion") is None
    assert food_reference.grams_per("cup", "onions") is None
    assert food_reference.grams_per("cup", "carrots") is None


#: Modifier-bearing phrases, with what ``to_grams`` must answer.
#:
#: **This table exists because the corpus above cannot contain this class.**
#: That corpus is harvested from repo literals and from the two vocabularies,
#: so by construction every line in it is a phrase one of the resolvers already
#: knows — and the sharpest failure of a name matcher lives exactly where the
#: phrase is NOT in any vocabulary: an ordinary modifier in front of an
#: ordinary food. A green scoreboard said nothing about "1 cup whole oats",
#: which an adversarial review measured at 37 g (a breakfast-cereal brand)
#: against the 97 g that shipped. Hence this second table, built by sweeping
#: 25 modifiers across the corpus foods and keeping every phrase whose answer
#: the modifier changes.
#:
#: These are scored through the whole of ``to_grams``, so a value may come
#: from the density table — which is the right outcome for "roasted oats": the
#: dataset has no such food, 97 g is what shipped, and the only wrong answer
#: was the 37 g brand-cereal row. ``None`` means every stage refuses, which is
#: right once a cook has said "canned" or "crushed": a weight for the plain
#: food is a weight for a different substance.
MODIFIER_LINES = (
    ("cup", "whole oats", 81.0),
    ("cup", "rolled oats", 81.0),
    ("cup", "roasted oats", 97.0),
    ("cup", "raw flour", 125.0),
    ("cup", "whole wheat flour", 125.0),
    ("cup", "raw sugar", 200.0),
    ("cup", "melted butter", 227.0),
    ("cup", "grated parmesan", 100.0),
    ("cup", "plain yogurt", 245.0),
    ("cup", "cooked quinoa", 185.0),
    ("cup", "canned lentils", None),
    ("cup", "chopped pickles", None),
    ("cup", "fresh cabbage", None),
    ("cup", "sweetened almonds", None),
    ("cup", "unsalted chocolate", None),
    ("cup", "crushed tomatoes", None),
    ("cup", "chopped celery", None),
    ("cup", "fresh parsley", None),
    ("cup", "fresh ginger", None),
    ("cup", "frozen peas", None),
    ("cup", "shredded coconut", None),
    ("cup", "ground cinnamon", None),
    # Both of these are myMeal's own scorer answering, and both are arguably
    # wrong: its state filter drops a dataset row that declares a state the
    # line did not ask for, but does not require a row to declare a state the
    # line DID ask for, so "dried garlic" is served raw garlic's weight. That
    # is pre-existing and unchanged by this work; pinned so it cannot move
    # silently while it waits for its own evidence.
    ("cup", "dried garlic", 136.0),
    ("cup", "canned milk", 245.0),
)


@pytest.mark.parametrize(("unit", "food", "expected"), MODIFIER_LINES)
def test_a_modifier_never_lands_on_an_unrelated_food(shipped, unit, food, expected):
    """One ordinary adjective in front of an ordinary food must not change
    which food it is. Mutation-checked: drop the ``>= _MIN_SCORE`` floor in
    ``_matched_food`` and nine of these fail, "whole oats" at 37.3 g."""
    grams = _strategy_c(shipped, unit, food)
    if expected is None:
        assert grams is None
    else:
        assert grams == pytest.approx(expected, rel=0.01)


def test_the_two_score_scales_still_mean_the_same_thing(shipped):
    """``_matched_food`` compares foodcore's published score against myMeal's
    ``_MIN_SCORE``, which is only sound while the two scales agree. They are
    both 0-100 over the same three tiers, and this is the assertion that fails
    if either library renumbers them.
    """
    # 100: the query IS the head noun, on both scales.
    assert shipped.resolve_food("honey").candidates[0][1] == _SCORE_EXACT
    assert food_reference._score(food_reference._forms("honey"), "Honey") == \
        food_reference._SCORE_EXACT_NAME

    # 70: inside the head segment. myMeal's floor sits exactly here.
    assert food_reference._MIN_SCORE == _SCORE_INSIDE_HEAD
    assert food_reference._score(
        food_reference._forms("flour"), "Flour, wheat, all-purpose") == \
        food_reference._MIN_SCORE

    # 50: somewhere in the description, and below the floor on both scales.
    weak = shipped.resolve_food("dried onions")
    assert weak.candidates[0][1] == _SCORE_ANYWHERE
    assert _SCORE_ANYWHERE < food_reference._MIN_SCORE



def test_a_pathological_line_cannot_mute_the_module_warning(shipped):
    """``resolve_food`` binds one SQL parameter per distinct token, so a line
    with a thousand words raises instead of refusing — and ``grams_per``
    catching it would latch ``_warned``, downgrading every LATER genuine
    foodcore failure to ``log.debug`` for the life of the process. A lookup
    refusing is fine; a lookup silencing the only warning channel is not.

    Mutation-checked: remove the token cap in ``_reference_food`` and
    ``_warned`` is True after the first line.
    """
    # reset_cache so _warned starts False whatever ran before; dataset_path is
    # already pointed at the shipped dataset by the fixture.
    food_reference.reset_cache()

    assert food_reference.grams_per("cup", " ".join(f"w{i}" for i in range(1100))) is None
    assert food_reference._warned is False
    # ...and the next real line still works, from the same process.
    assert food_reference.grams_per("cup", "flour") == 125.0

def test_to_grams_multiplies_the_reference_weight_through(shipped):
    """End to end, through the function every caller actually uses — the
    scoreboard is per-unit, and a quantity still has to reach the answer."""
    assert units.to_grams("2 cups flour") == pytest.approx(250.0)
    assert units.to_grams("1 cup rice") == pytest.approx(185.0)
    assert units.to_grams("3 tbsp olive oil") == pytest.approx(40.5)
