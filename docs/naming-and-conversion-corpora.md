# Naming & volume→weight: the held-out corpora

Two labelled test sets, cross-validated against independent sources, used to
**judge** work on ingredient naming and volume→weight conversion rather than to
guide it.

| | |
|---|---|
| `backend/tests/fixtures/naming_corpus.py` | name abstraction: strip vs preserve |
| `backend/tests/fixtures/volume_weight_corpus.py` | cup→gram, with ambiguity and declines |
| `backend/tools/validate_corpora.py` | audits the labels, then scores the code |
| `backend/tests/test_naming_and_conversion_corpus.py` | the invariants, in CI |
| `backend/tools/corpus_baseline.json` | the coverage ratchet |

## Why they are held out

The agents implementing the fixes did not write these. If the implementer also
writes the test set, it grades its own homework — the corpus converges on what
the code already does, which is exactly the thing under question.

## Why two sources, not one

A corpus built from the dataset the code reads cannot tell **"the code is
wrong"** from **"my expectation is wrong."** It just re-asserts the dataset.

So every conversion expectation must agree between:

- **USDA SR Legacy** portion rows, located by *searching descriptions*
- **King Arthur Baking's** published cup-weight chart, plus standard dairy
  figures

Agree within 12% → ground truth (USDA's value, since it has citable
provenance). Disagree → **quarantined**, never silently averaged.

33 candidate foods went in; 28 agreed, 1 was quarantined, 4 needed re-deriving.

### This caught five of my own errors, not the code's

FDC ids typed from memory pointed at:

| intended | actually | caught by |
|---|---|---|
| molasses | a MARS CocoaVia candy bar | first search |
| cornmeal | millet | first search |
| tahini | pumpkin seeds | first search |
| quinoa (uncooked, 170 g) | the **cooked** row, 185 g | the label audit |
| shredded cheddar, parmesan | ids with no cup portion at all | the label audit |

Hence the rule now at the top of the fixture: **never hand-type an FDC id.**
Phase 1 of the validator re-checks every citation against the dataset, so a
future USDA release that restates a portion moves the corpus deliberately and
visibly instead of just turning a test red.

## Four separate numbers, never one score

Measured on `main`:

```
normalisation   9/13    does "Wegman's Avocados" reduce to avocado
boundaries     20/20    does "almond milk" survive, AND for the right reason
catalog        19/26    does the canonical term exist at all
conversion     21/31    does 1 cup flour weigh 125 g
declines         2/2    does an unanswerable request return None
equivalence      2/3    does a functional qualifier keep the weight
discrimination   3/5    do near neighbours stay distinguishable
```

Blending these would have hidden the finding that makes the work tractable.

### The normaliser was never broken

Every one of the four normalisation failures is a **catalog** gap. The control
group settles it — identical input shapes, head word the catalog knows:

```
Wegman's Avocados    -> unknown          Wegman's Butter      -> butter  (split)
Organic Raspberries  -> unknown          Organic Butter       -> butter  (split)
ripe avocado         -> unknown          ripe banana          -> banana  (split)
Heinz Ketchup        -> unknown          Trader Joe's Bananas -> banana  (split)
```

Possessives, plurals, multi-word brands and grade words are all handled. The
fix is **data** (`SEED_FOODS` has 135 entries and does not know `avocado`,
`raspberry`, `ketchup`, `blueberry`, `strawberry`, `mayonnaise`, `mustard`), not
logic. A blended score would have read as "naming is 70% broken" and sent
someone to rewrite a resolver that is correct.

## Three things the corpus design itself had to get right

**1. Declining is a first-class correct answer.** Only **2,582 of 7,793** SR
Legacy foods (33%) carry a cup portion, so "no answer" is the *common* case.
`tahini` has four entries and not one cup portion; the right output is
unresolved, never a plausible number. A suite that cannot tell *declined* from
*answered wrongly* pressures someone into making the engine guess.

**2. Ambiguity is not a coverage gap.** Bare `chickpeas` spans **152–240 g**
across forms (drained 152 / cooked 164 / raw 200 / canned-with-liquid 240). Any
single confident number is wrong, and a naive unqualified-row pick silently
returns the with-liquid 240. The corpus requires a *decline*.

**3. A tolerance wide enough for scooping is wider than some foods are apart.**
This is the one that drew blood — see below.

## The bug the tolerance hid

`bread flour` returned **125 g/cup** (plain flour) where USDA says **137**.

The absolute-value case **passed anyway**: the gap is 9.6% and the scoop
tolerance is 12%. A value check cannot police near neighbours. Only an explicit
`DISTINCT_PAIRS` case — *these two must differ* — caught it, at a measured gap
of 0%.

Root cause was one missing word: `FUNCTIONAL_QUALIFIERS` held the British names
(`strong`, `wholemeal`) but not the American `bread`. Consequences were in both
lanes — the shopping list said "flour" (wrong purchase) and conversion was
silently 10% light.

Fixed. The shopping lane now preserves `bread flour`; conversion declines rather
than answering wrongly, with 137 g left to the coverage work.

## The ratchet was measuring the wrong thing

After that fix the coverage gate reported a **regression**: conversion 22 → 21.
It counted *answers*, so trading a silently-wrong 125 g for an honest decline
looked like a loss.

A gate that rewards guessing is worse than no gate — in an engine whose entire
premise is that an unknown is never a number. The ratchet now counts
*correctness*, and a value inside tolerance that fails discrimination is
counted **wrong**, not right.

## Shopping identity ≠ weight identity

`fresh spinach` and `unsalted butter` stay intact, and that is correct: fresh vs
frozen spinach is a different *purchase*. But unsalted butter is the same
*weight* as butter (both 227 g/cup, verified).

One `normalize()` cannot answer both questions, and it does not try — the
shopping lane keeps the qualifier while the conversion lane sees through it.
`WEIGHT_EQUIVALENT` pins that so a future change to `FUNCTIONAL_QUALIFIERS`
cannot fix shopping by breaking weighing.

I mislabelled both cases on the first pass, in exactly that direction.

## Asserted vs reported

**Asserted** (never regress): material boundaries, declines, equivalence,
discrimination, and the correctness of any value the engine *does* return.

**Reported** (a ratchet, may rise and never fall): catalog and conversion
coverage.

Coverage is meant to climb over several changes. A suite that stays red until
coverage is perfect gets deleted — and then the invariants stop being guarded
too.

## Vacuity guards

Two cases are written so they cannot pass trivially, per the mutation rule:

- `WEIGHT_EQUIVALENT` fails when *both* sides are `None` — `None == None` would
  pass with the feature deleted. `chopped walnuts` currently fails on exactly
  this, honestly.
- `PRESERVE` asserts the *reason*, not just the answer. `almond milk` surviving
  via `unknown` (nothing matched) looks identical to surviving via `material`
  (the guard fired) — until someone adds `milk` to the catalog and the first one
  starts stripping silently. `greek yogurt` passes via `unknown` today and is
  flagged as a weak pass.
