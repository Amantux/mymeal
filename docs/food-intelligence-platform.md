# Food Intelligence — what to take from the spec, and where it goes

> Response to the *Food Intelligence Platform* spec v1.0, assessed against what
> myMeal, Edibl and HomeHoard actually contain today (verified by reading the
> code, not from memory). Status: **proposal**. Nothing here is built.

## 1. The headline recommendation

**Do not build this inside myMeal.** It is a fourth sibling service — a shared
*food reference* backend that myMeal and Edibl both consume — for three reasons
that are already true in the code:

1. **Both apps already need the same thing and already duplicate it.** Edibl has
   a dimension-aware `Quantity` value object with its own unit registry
   (`edibl/backend/app/services/quantity.py`, 240+ lines) and an Open Food Facts
   barcode adapter (`services/barcode.py`). myMeal has its own unit parser and a
   **hand-maintained table of 41 densities** (`units.py::_DENSITY_G_PER_ML`).
   Two unit systems, one household. The SOP's cross-app rule (§8) says shared
   policy lives in ONE place.
2. **The split is already drawn and documented.** `myMeal/backend/app/services/
   inventory.py` opens with: *"Inventory is owned by the companion Edibl app,
   not by myMeal. myMeal keeps the matching logic (which is about recipes, its
   own domain) but no longer stores a pantry."* The spec's FR-12 (pantry) and
   FR-11 (barcodes) are **Edibl's**, not myMeal's — and already partly built.
3. **There is a precedent for a shared service add-on**: `shared_postgres`
   provisions a database per app over Supervisor discovery. A `foodcore` add-on
   is the same shape — one dataset, many consumers, discovered not configured.

So the deliverable is **`foodcore`**: a read-mostly reference service. The apps
stay the system of record for *their user's* data (recipes, lots); foodcore owns
*reference* data (what a gram of flour is), which is identical for every
household on earth and therefore has no business being group-scoped.

## 2. Spec vs. what exists today

| Spec FR | myMeal today | Edibl today | Verdict |
|---|---|---|---|
| FR-01 canonical ingredients | `Food` (name, aliases JSON, aisle, `classification`, `allergens` JSON) + `food_resolve` with the material-boundary rule ("almond milk" ≠ milk), ADR 0001 | `FoodConcept`, same contract | **Exists, group-scoped.** Needs a *reference* tier beneath it, not a replacement |
| FR-02 quantities / unit conversion | `units.py` (539 lines): parse, canonicalise, scale, 41 densities | `quantity.py`: dimensions, uncertainty, registry | **Duplicated, and thin.** The strongest case for foodcore |
| FR-03 recipe ingestion | URL + JSON-LD + microdata + paste + archive import (Mealie/Tandoor/Paprika) + deterministic parser | — | **Ahead of the spec.** Keep in myMeal |
| FR-04 pantry search | `inventory.py::rank_recipes` over Edibl's stock | stock/onhand | **Exists**, not quantity-aware yet |
| FR-05 nutrition | **LLM estimate** stored as JSON (`services/ai/nutrition.py`) | — | **Direct conflict with the spec — see §3** |
| FR-06 allergens | `Food.allergens` list; no rule engine, no evidence, no unknown state | allergen-ish classification | **Data field without a policy.** Safety gap |
| FR-07 dietary profiles | none found | — | Absent |
| FR-08 classification | cuisine/meal tags, AI-assisted | — | Partial |
| FR-09 meal planning | meal plans, AI fill-the-week, ICS feed | — | **Exists**, not constraint-solved |
| FR-10 shopping lists | aggregation, canonical grouping, aisle order, pantry deduction via Edibl | — | **Exists** |
| FR-11 barcode / UPC | — | **Open Food Facts lookup, opt-in, degrades to None** | **Edibl's.** Extend there |
| FR-12 pantry inventory | deliberately absent | lots, dates, placement, reorder, spoilage estimation | **Edibl's, largely built** |

Read that table as: the spec is ~60% already shipped across the estate, mostly in
the right places. What's missing is not features — it's a **reference-data floor**
under the two things that are currently guesses (grams, nutrition) and a
**policy engine** for the one thing that is currently unsafe (allergens).

## 3. The two findings that matter most

### 3.1 Nutrition is currently an LLM estimate

`services/ai/nutrition.py` asks a model for calories/macros and stores the JSON.
The spec says plainly: *"Do not use LLMs as the final authority for allergen
compatibility or nutritional arithmetic."* I agree with the spec. Today's
behaviour is defensible as a stopgap for a household app — but it is arithmetic
performed by a text model, it carries no provenance, and `0` currently means both
"zero" and "didn't know".

The fix is not "remove AI". It is: **USDA per-100 g values × edible grams**,
deterministic, with the LLM demoted to resolving *which* USDA food a line means
(exactly the spec's §8 workflow). Where no match exists, return *unknown*, not 0.

### 3.2 Empty `allergens` means "unknown", and nothing says so

`Food.allergens` is a JSON list seeded for known foods. For everything a user
typed themselves it is `[]`. There is no allergen filtering in the API yet — so
nothing is *currently* unsafe. But the moment someone adds "hide recipes with
nuts", `[] == safe` is the default reading, and that is the failure the spec's
FR-06 exists to prevent. Build the four-state model (`contains` / `may contain` /
`unknown` / `no known ingredient identified`) **before** the first filter ships,
not after.

## 4. What to take, right-sized

The spec targets 1M recipes, OR-Tools, pgvector, several million products. These
apps run on a NUC in someone's cupboard next to Home Assistant. Proportionality:

**Take:**
- **USDA FoodData Central** (CC0 — no licence friction, no attribution plumbing).
  Specifically the *portion weights* table, which is what turns "2 cups flour"
  into grams for 10,000+ foods instead of 41.
- **Open Food Facts** — already in Edibl. Extend, don't re-add.
- **FoodOn** — as a *vocabulary* for aliases/hierarchy only. Not as a classifier.
- The spec's **provenance model** (§5.3) nearly verbatim. It is the best part of
  the document and costs almost nothing to adopt early: source, method, version,
  confidence, and *measured vs declared vs calculated vs inferred vs estimated*.
- The spec's **unresolved-conversion** rule (FR-02): refuse rather than fabricate.
  This matches how this codebase already treats ambiguity everywhere else.

**Skip, deliberately:**
- **RecipeNLG (2.2M), RecipeDB2, Food.com** — research licences, gigabytes, and
  near-zero value for a household that imports its own recipes. The spec itself
  flags the rights problem. Revisit only if "discover new recipes" becomes a goal.
- **OR-Tools / MIP meal planning** — a 7-day plan for one household is not a
  constraint-programming problem. Current heuristics plus "explain infeasibility"
  gets the same user outcome.
- **pgvector / semantic search** — defer until deterministic alias matching
  demonstrably fails. It already works well (`food_resolve`).
- **Microservice split** — the spec's own §9 says start as modules. Agreed:
  foodcore is one Flask app, same stack as its siblings.

## 5. Extension capacity that already exists

These are the seams to build on, all present today:

| Seam | Where | Reuse for |
|---|---|---|
| Provider registry + ABC | `services/ai/registry.py` | A **food-data provider registry** — `usda`, `off`, `local`, each a class + one line, same shape |
| `Field` settings registry | `settings.py` | `foodcore_url`, `foodcore_token`, dataset refresh cadence — with generated docs + add-on schema for free |
| Supervisor discovery | `ha_discovery.py`, `shared_postgres` | foodcore advertises host/port/token; apps auto-configure |
| Sibling client w/ graceful absence | `services/edibl.py` | `FoodcoreClient` — **must degrade to today's behaviour when absent** |
| MCP tool catalog | `mcp_server.py` | `convert_ingredient_quantity`, `calculate_recipe_nutrition` as read-only tools |
| Shared DB provisioning | `shared_postgres` add-on | foodcore gets its own DB + least-privilege role |
| ADR 0001 food contract | `docs/adr/` | Extend to cover the reference tier |

**The load-bearing constraint:** every app must keep working with foodcore
absent. It is an *enrichment* service, not a dependency. That rule is what makes
this safe to ship incrementally and is non-negotiable.

## 6. Staged plan (each stage independently shippable)

**Stage 0 — unify quantities (no new service).** Pick one unit model between
`units.py` and Edibl's `Quantity`, port it, delete the loser. This is owed
regardless of foodcore and removes the duplication that makes everything else
ambiguous. *Exit: one unit system across two apps.*

**Stage 1 — foodcore skeleton + USDA portions.** Add-on, own DB, discovery,
`POST /v1/measurements/convert`. myMeal's `to_grams()` consults it, falls back to
the 41-density table when absent. *Exit: "2 cups flour" → 240 g with provenance,
and "1 can tomatoes" honestly returns unresolved.*

**Stage 2 — deterministic nutrition.** USDA per-100 g × edible grams, per serving,
with completeness/uncertainty. The LLM path becomes the *matcher*, not the
arithmetic, and the stored estimate is clearly labelled as such until replaced.
*Exit: nutrition carries provenance; unknown ≠ zero.*

**Stage 3 — allergen evidence engine + dietary profiles.** Four-state model,
evidence retained, rule versioned. Ship the engine before any UI filter.
*Exit: a hard allergen filter that refuses to call unknowns safe.*

**Stage 4 — quantity-aware pantry search.** Edibl already holds quantities; with
Stage 0+1 the matcher can finally compare *amounts*, not just names.
*Exit: "cook now" means you actually have enough.*

Stages 0–2 are where essentially all the user-visible value sits for a household.
3 is a safety investment. 4 is the payoff that needs 0–1 first.

## 7. Open questions for the owner

1. Is "discover recipes you don't have" ever a goal? If no, the entire restricted
   recipe-corpus tier (§3.2 of the spec, and most of its licensing complexity)
   drops out permanently.
2. Is foodcore a **fourth add-on**, or a module inside Edibl (which already owns
   products and barcodes)? A module is cheaper; a separate add-on keeps reference
   data alive when Edibl isn't installed, which matters for myMeal-only users.
3. Does anyone else install these? If this estate is one household, the licensing
   tiers (§12) collapse to "attribute OFF, stay CC0 elsewhere" and a lot of the
   spec's governance machinery is unnecessary.
