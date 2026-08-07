# ADR-0001: One food resolver; a split never crosses a material boundary

**Status:** accepted · **Date:** 2026-08-03

## Context

`_find_or_create_food` (`backend/app/api/recipes.py:45,66`) matches on the
**exact** lowercased name and nothing else. "Vietnamese Cinnamon", "cinnamon"
and "Cinnamon, ground" become three `Food` rows, so shopping lists don't
consolidate and "what can I cook" misses matches. `Food.aliases` exists and is
written by the Foods API but is **never consulted when a food is created**, so it
cannot prevent a duplicate — a bypassed policy, not a missing one.

Four unrelated pieces of code already answer "reduce an ingredient string to its
food identity", each differently:

| where | what it does |
|---|---|
| `services/conversions.py:83` `food_term` | strips noise words, **keeps the last word** |
| `services/inventory.py:27` `_ingredient_haystack` | concatenates display + food + aliases, naive substring |
| `services/shopping.py:42` | `parse_line(text)["rest"].lower()` |
| `services/units.py:246` `_density_for` | longest-keyword substring over a density table |

The last-word rule is actively wrong and reachable: `"2 sticks peanut butter"`
keys to `("stick", "butter")`, so a learned "1 stick butter = 113 g" is applied
to peanut butter. `"1 can coconut milk"` and `"1 can evaporated milk"` both key
to `("can", "milk")`.

Edibl solved the same problem for stock (its ADR-0003). Its `FoodConcept` is
"canonical ingredient identity — the broad 'what' a recipe cares about ('milk',
'green onion'), separate from a purchasable Product", and its rule is:

> Mutations must **not guess** when candidates differ materially (allergen,
> dietary, item_type, concept). **Substring never crosses an allergen/item_type
> edge.**

## Decision

**One `services/food_resolve.py`**, in two layers, and every one of the four
matchers above delegates to it.

- **`normalize(raw)` — pure, no DB.** Safe on hot read paths. `units.to_grams`'s
  `learned` parameter is documented (`units.py:230`) as a parameter rather than
  an import precisely to keep this module DB-free; a DB-backed resolver inside
  `food_term` would fire a query per ingredient line on every weight render.
- **`index(gid)` + `resolve()` / `resolve_for_mutation()` — DB-backed**, a
  memoising closure over the group's Foods, ported from Edibl's
  `services/matching.py` (same score tiers, same `Candidate`/`Resolution`, same
  singularizer, so "eggs" ~ "egg" identically in both apps).

**A split is emergent, not a curated phrase map.** "Vietnamese cinnamon" ends
with the known food "cinnamon", so it splits and the leftover text *is* the
qualifier. Longest suffix wins, so "extra virgin olive oil" → `olive oil`, never
`oil`.

**Three guards decide when a split is refused**, in this order:

1. **The whole phrase is itself a known food** — `peanut butter`, `sour cream`,
   `black pepper`, `sweet potato`, `green onion`. Exact match beats any split.
   **Seeding a compound is how you protect it.** A first draft also carried a
   separate `PROTECTED_COMPOUNDS` list; deleting it failed no test, because every
   entry was already a seed food. One list, not two.
2. **The qualifier text is itself a food, materially different from the head** —
   different `classification` or `allergens`. This is Edibl's rule, and it
   generalises: `cashew butter`, `oat milk`, `rice vinegar`, `chicken stock`,
   `olive oil` are all refused without being listed anywhere. **Both halves of
   the comparison earn their place**: `rice flour` and `flour` are both `grain`,
   so the allergen list is the only thing stopping a coeliac being told rice
   flour is flour.
3. **The qualifier is functional** — if buying the canonical instead would change
   what you cook, it does not split: `self-raising flour`, `double cream`,
   `condensed milk`, `smoked paprika`, `unsalted butter`.

**Writing a `food_id` is a mutation, so it never guesses.** Ambiguous → leave
`food_id` NULL exactly as today and surface candidates.

**The variety is kept**, in `recipe_ingredients.qualifier` — not in `note`, which
means preparation ("finely chopped") and is *already* contaminated with variety
words by two AI prompts. `display` is never rewritten in the database.

⚠️ `_NOISE` contained "unsalted", "whole", "plain" and was **correct there** —
salted and unsalted butter weigh the same. It must never be promoted into the
qualifier lexicon. Two lists that look alike with opposite safety properties.
It now lives as `food_resolve.PREPARATION_WORDS` (three callers needed it and had
to agree), with that warning kept beside it.

## What implementation changed about this design

Recorded because each was found by measuring, not by reasoning, and each would
otherwise be re-litigated.

**One key, three callers.** `match_key` (preparation words stripped, then
canonicalised) backs the learned-weight cache, the density lookup and inventory
coverage. They must agree or they contradict each other in front of the user.
It was briefly named `weight_key`, which described one of the three.

**A canonical-only match was too strict, and only measurement showed it.**
Matching density on the canonical name alone lost 36 of 67 lookups on a realistic
corpus — including correct ones (sesame oil, chicken stock, maple syrup). The
shipped rule keeps a head-noun fallback *gated by the material boundary*: that
gate is the only thing separating "sesame oil is oil" from "peanut butter is not
butter", which a substring search cannot tell apart. Final result: 0 lookups
lost, 13 wrong values corrected.

**The boundary guard fails OPEN on unknown qualifiers.** It can only refuse a
qualifier it knows, so "hazelnut butter" inherited butter's density while
"walnut oil" was correctly refused. The fix was declaring the remaining tree
nuts, not making the guard fail closed — closed would also refuse "sunflower
oil" and "maple syrup". **Coverage of the seed list is therefore a safety
property here, not just a quality one.**

**Canonicalise the KEY, not always the LABEL.** Rewriting a display name the
lexicon does not understand mangles it: "6 bone-in chicken thighs" came back as
"bone in chicken thighs" because normalisation strips punctuation. Renaming is
gated on `is_known`; unknown names still group by canonical key so two spellings
merge, but are shown exactly as written.

**A lookup table must be keyed the way lookups arrive.** The density table said
"yogurt" while lookups canonicalised to "yoghurt", so that row existed and could
never be hit. Tables consulted with canonical keys are now re-keyed canonically.

**Explicit creation is not deduplication.** `POST /foods` matches on exact name
or alias only, never on the canonical key — otherwise a household could never
deliberately keep "Vietnamese cinnamon" as its own food, which is precisely the
guarantee find-or-create protects. Collapsing varieties is the merge endpoint's
job, behind a confirmation.

## Consequences

- **+** Kills four divergent resolvers and three live bugs (peanut-butter
  weights, "rice" covering "rice vinegar", duplicate `Food` rows).
- **+** The failure mode is always "didn't normalize", never "merged two
  different ingredients".
- **+** `Food` gains `classification` and `allergens`, making it the same object
  as Edibl's `FoodConcept`. Aligning them would let Edibl answer "can I
  substitute this?" for myMeal. Not built yet.
- **−** A curated seed list needs maintaining. It is data, not logic, and the
  guards mean an omission degrades to "not normalized" rather than to a wrong
  answer.
- **−** Merging existing duplicate `Food` rows is a user-facing proposal, not a
  migration. See Edibl ADR-0004: low-confidence must not silently overwrite.
- **−** Cache keys changed, so existing `unit_conversions` rows stop being hit.
  The poisoned ones are not repairable — the original text was never stored — so
  they age out rather than being migrated.

## Related

- Edibl ADR-0003 (one matching service; material boundaries) and ADR-0004
  (provenance + confidence) — `/root/edibl/docs/stock-redesign/adr/`.
- Edibl `12294e6` fixed the plan-demand contract this exposed: a renamed
  ingredient used to leave its old row behind and double a recipe's demand.


## Addendum (Aug 2026): aliases are a JSON list with a write policy

The comma-separated `Food.aliases` column is gone (migration 0014). Storage is
a JSON list — the shape `allergens` already used and the shape Edibl's
`FoodConcept.aliases` has always had. The wire contract did not change
(`food_out` emitted a list all along; nothing external consumes aliases).

**The invariant lives at the write chokepoint, not in the schema.**
`food_resolve.set_aliases` / `claim_index` / `alias_key` enforce:

- one folded key resolves to at most one food per household (refuse, never
  steal; deterministic winner by `(created_at, id)`);
- a term the seed lexicon knows as a DIFFERENT food cannot become an alias
  ("salt" can never alias cinnamon), while a term sharing the canonical is
  exactly what an alias is ("cilantro" for coriander);
- the stored form is the pre-comma identity part — all `normalize_text` ever
  lets the ranker see; storing text the resolver silently discards is how
  "salt, kosher" hid a wrong resolution behind a correct-looking list;
- one equivalence rule: `_existing_match` now folds like the ranker does, so
  "Cilantros" cannot create a duplicate the ranker treats as the same food.

**A relational `food_aliases` table was evaluated and rejected — for now.**
The `unit_conversions` shape (unique constraint + provenance + pending gate)
is the textbook model, but a DB constraint could only see aliases — names live
on `foods`, and the name-vs-alias collision is the common half. At ~10 alias
rows per household with no alias-editing UI, the chokepoint wins. Promote to a
table when any of these lands: an alias-editing UI, a provenance consumer
(alias review queue), or household alias counts in the hundreds.

Hard-won migration facts, recorded because none were visible from SQLite:
`PRAGMA foreign_keys` is a no-op inside a transaction (the backfill's UPDATEs
open one — use `autocommit_block`); Postgres returns `json` columns parsed and
validates them on write (downgrades must read-before/write-after the alter);
Postgres refuses `ALTER TYPE` when the column DEFAULT cannot be auto-cast
(`USING` covers values, not defaults — drop it first). And `0001_baseline` is
metadata-driven, so "the column type at revision N" depends on which baseline
built the database — data backfills must be gated on data predicates, not
column types.
