# foodcore and its consumers — what the shape is now, and what to fix

> Written 2026-10-24 after extracting foodcore and wiring both apps to it.
> Everything here is measured against the current trees, not recalled.
> Status: **assessment + recommendations**. Items 1–3 are not yet built.

## What actually got built

A shared reference engine (`github.com/Amantux/foodcore`, zero runtime
dependencies, bundled USDA SR Legacy snapshot) consumed by two apps that pin it
by commit SHA. myMeal uses it for gram conversion, deterministic nutrition and
recipe screening; Edibl for food-aware conversion and product allergens.

That much works. What follows is what the extraction *revealed* — problems that
were invisible while the code lived in one repo.

## The pattern behind every defect found this week

Each one is the same shape: **the shared library owns the knowledge, but each
host app wrote its own code around it** — and that glue is where the bugs live.

| defect | where it lived | how it was found |
|---|---|---|
| Dataset expanded to a predictable world-writable path; symlink poisoning gave 1700 g for "1 cup quinoa", stamped `measured`/`CC0-1.0` | host glue, myMeal only | reviewing my own work; Edibl had independently hardened it |
| Truncated gzip raises `EOFError`/`zlib.error`, neither an `OSError` — cleanup unreachable, ~10 MB leaked per boot | host glue, myMeal only | same port |
| Engine declares 26 nutrients, importer maps 11, only 5 ids agree | *inside* foodcore | measuring a real import |
| Consumers hand-write nutrient translation tables | host glue, both | myMeal's integration flagged it as a blocker |

Three of the four are duplication, not logic. Measured:

```
host-side glue around the library
  myMeal  services/food_reference.py   1161 lines
  Edibl   services/food_reference.py    838 lines

both independently reimplemented the same six concerns:
  gzip expansion · lstat symlink checks · euid-private dirs
  atomic rename · result caching · dataset loading
```

A security-sensitive routine written twice is a security bug waiting for the
copy nobody reviewed. That is exactly what happened.

---

## Recommendation 1 — foodcore owns dataset PROVISIONING, not just data

**The problem.** foodcore ships a gzipped dataset and says "the host owns the
path". Both hosts therefore wrote: find the asset inside the installed package,
create a private directory, expand atomically, detect a hostile symlink, cache
the handle, degrade when any of it fails. ~30 lines in myMeal, ~54 in Edibl,
same six concerns, **one of them exploitable for weeks**.

The "host owns the path" rule was right about *configuration* and wrong about
*mechanism*. Where the file goes is the host's business; safely turning a
shipped `.gz` into an open SQLite handle is not — it is one problem with one
correct answer, and it belongs with the asset.

**The fix.** A single entry point in foodcore:

```python
ds = foodcore.ensure_dataset(cache_dir=None, override=None)
```

- `override` — the host's configured path, used verbatim if set
- `cache_dir` — where the host wants expansion to land; default a private
  per-euid directory under the system temp dir
- returns a `Dataset`, or the bundled minimal one, and **never raises**

Both hosts then delete their expansion code and keep only policy: which path,
whether the feature is enabled, what to do when it degrades. ~80 lines of
duplicated security-sensitive code become one tested implementation, and the
symlink class of bug stops having two places to hide.

**Caveat worth stating:** this moves a filesystem concern into a library whose
selling point is purity. The mitigation is that it stays *optional* —
`load_dataset(path)` keeps working untouched, and `ensure_dataset` is a
convenience on top. A host with unusual needs is not forced through it.

## Recommendation 2 — one resolver, not two — **DECIDED 2026-10-24**

> **Outcome: (b) with a measured exception.** foodcore's resolver plus the
> household vocabulary now answers first in myMeal; its own scorer is kept as a
> documented fallback for the tail, and the density table stays last. The
> bake-off is `backend/tests/test_conversion_bakeoff.py` — 122 harvested recipe
> lines, scored for coverage and for distance from the weight a cook expects:
>
> ```
> strategy                        converts   correct   wrong   overclaims
> A  myMeal's scorer -> table       85/122    77/105       7        0/11
> B  foodcore -> table              89/122    82/105       6        0/11
> C  foodcore -> myMeal -> table    92/122    85/105       6        0/11
> ```
>
> Neither (a) nor a clean (b) survived contact with the numbers. (a) is out:
> A's scorer cannot answer a bare cooking word at all, and six of its answers
> that *look* like hits are the density table rather than the dataset. A clean
> (b) is out too: deleting myMeal's scorer costs three correct conversions that
> the 60-word vocabulary does not cover ("quinoa", "parsley", "vinegar"), and
> the constraint was that nothing working may stop working. So the duplication
> is not removed, it is **ordered** — and the order is now a number in a test
> instead of whichever file you happened to read.
>
> An earlier cut of this scored 94/88 by also accepting foodcore's lone *weak*
> matches, and adversarial review proved what that bought: "1 cup whole oats"
> answering 37 g from a breakfast-cereal brand where 97 g shipped. Killing that
> class gave back one conversion and three correct answers (garlic powder,
> powdered sugar, cream of tartar — all words the vocabulary should cover), and
> honouring the dataset's own ambiguity refusal gave back one more
> conversion. **The lesson is
> about the corpus, not the resolver:** a corpus harvested from the two
> vocabularies cannot contain an out-of-vocabulary phrase, which is exactly
> where a name matcher fails worst, so the test file now carries a second
> table of modifier-bearing lines ("whole oats", "canned lentils", "fresh
> cabbage") built by sweeping modifiers across the corpus foods.
>
> myMeal therefore keeps three of its own gates as rejects on foodcore's
> answers — an evidence floor on the published match score, the state-word
> filter on matcher results, and honouring `portion_grams`'s
> `qualifier_required` before letting `convert` derive a density past it.

### The original assessment

**The problem, measured today:**

```
Edibl   -> ds.resolve_food()          foodcore's confidence-tiered matcher
myMeal  -> ds.search_foods() + its own scorer
           (_score, _forms, _MIN_SCORE, an agreement gate, state-word filters)
```

Two algorithms answer "which food is this?" against the same dataset. This is
the §11 pattern from the SOP — *a bypassed policy and a missing policy look
identical from the symptom side, and the fixes are opposite* — and it is the
drift the shared engine was created to prevent.

Neither is obviously wrong. myMeal's gates were built for a real reason: against
7,793 foods a bare `resolve_food` refused nearly everything useful, and myMeal
needed an answer for "2 cups flour". foodcore later grew the household
vocabulary that solves the same problem more honestly.

**The fix is to decide, once.** Either
(a) myMeal's gates are better → move them into foodcore and delete the host
copy; or
(b) foodcore's resolver plus the household vocabulary is sufficient → myMeal
adopts it and deletes its scorer.

What must not continue is both. The decision needs a bake-off on real recipe
lines, scored against what a cook would say — not an opinion.

### What the bake-off asks foodcore for

Three findings came out of running it. All three are library-side; the second
and third have host-side *mitigations* in myMeal now (an evidence floor, and
honouring `portion_grams` before `convert`), which is a plaster over a
contract, not a fix:

1. **The household vocabulary is eight words short of the common case.**
   "breadcrumbs", "lentils", "chickpeas", "cornmeal", "cream cheese", "tahini",
   "polenta" and "shredded cheddar" each have a usable SR Legacy row with a
   published cup or tablespoon weight, and neither resolver can reach it —
   USDA writes "Bread, crumbs, dry, grated, plain" and "Seeds, sesame butter,
   tahini, …". These are the ten lines the brief measured as dead; two of them
   (almonds, walnuts) are qualified-only and correctly refuse.
2. **`resolve_food` is confident too easily — and inconsistently with
   itself.** With two or more candidates it requires `top >= 70` and a 30-point
   gap; with exactly one it returns `confident=True` at any score, including
   50 ("the tokens appear somewhere in the description"). So "dried onions"
   resolves, confidently, to "Snacks, potato chips, made from dried potatoes,
   sour-cream and onion-flavor", and "whole oats" to a branded breakfast
   cereal. Measured on the shipped dataset, 45 such lone-score-50 matches
   produce a weight. The one-line consumer-side floor is in `_matched_food`;
   the real fix is for the lone-candidate shortcut to apply the same floor
   every other path in that function already does.
3. **`convert` and `portion_grams` disagree, and `convert` derives too
   eagerly.** `portion_grams` refuses "Sugars, powdered" for a cup
   (`qualifier_required`: sifted 100 g, unsifted 120 g) while `convert`
   returns 120 g for the same food and measure. And `_grams_per_ml` takes
   whichever volume portion it finds first rather than the nearest measure, so
   a teaspoon of grated parmesan comes back as 2.1 g derived from the cup row
   when the food publishes a tablespoon implying 1.67 g.

## Recommendation 3 — version it like a dependency, because it is one

**Today:** `version = "0.1.0"`, **zero git tags**, both consumers pinned to a raw
40-character SHA, and a breaking change (the nutrient vocabulary) already in
flight. "Which foodcore is Edibl on?" is answerable only by diffing two
requirements files and reading a commit.

**The fix, in order of value:**

1. **Tag releases and pin tags.** `foodcore @ git+...@v0.2.0` tells a reader at a
   glance what two apps are sharing; a SHA does not.
2. **Semantic versioning with a real contract.** The nutrient rename is a
   breaking change and should ship as `0.2.0` with the old ids gone — not
   quietly aliased, which is how two vocabularies start again.
3. **A compatibility assertion at import.** Each consumer declares the major it
   expects, and foodcore raises a clear error on mismatch. A SHA bump that
   silently changes a key vocabulary is otherwise indistinguishable from a
   working install until a nutrition panel renders empty.

## Recommendation 4 — contract tests both consumers run

The three-step bump (commit → CI → move the SHA in both) is procedure, and
procedure has failed twice this estate already. A small suite living in
foodcore and executed by both consumers' CI — "these ids resolve, these units
convert, absence is never clearance" — turns the obligation into a failing
build. Cheap, and it is the only item here that makes the cross-app rule
mechanical rather than remembered.

---

## Sequencing

1. Nutrient vocabulary (in flight) — it is already a breaking change; do the
   version/tag work (3) in the same release rather than bumping twice.
2. `ensure_dataset` (1) — biggest duplication win, removes a live bug class.
3. Contract tests (4) — small, and makes 1 and 2 safe to iterate on.
4. The resolver bake-off (2) — the largest and least urgent; it needs evidence,
   not a decision made in passing.

## What NOT to do

- **Do not merge the two apps.** Recipes and pantry are genuinely different
  domains with different lifecycles; the duplication is in the *glue*, and that
  is fixable without a merge.
- **Do not add a service.** Every problem above is a library and packaging
  problem. An HTTP hop between an app and its own unit conversion buys nothing
  and costs a deployment.
- **Do not alias the old nutrient ids "for compatibility".** Two names for one
  thing is the bug being fixed.
