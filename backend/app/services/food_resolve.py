"""Canonical food identity: "Vietnamese Cinnamon" → cinnamon + "Vietnamese".

The pure half of the food resolver — lexicon only, **no database**, safe to call
from hot read paths (``units._density_for``, ``conversions.food_term``,
``inventory._ingredient_covered``). The DB-backed half lives alongside it and
ranks against a household's own ``Food`` rows.

See ``docs/adr/0001-canonical-foods-and-qualifiers.md``. The short version:

A split is **emergent**, not a curated phrase map. "Vietnamese cinnamon" ends
with the known food "cinnamon", so it splits and the leftover text is the
qualifier. Longest suffix wins, so "extra virgin olive oil" resolves to
``olive oil`` and never to ``oil``.

Three guards decide when a split is refused, and the order matters:

1. the whole phrase is itself a known food (``peanut butter``, ``sour cream``,
   ``sweet potato``, ``green onion``);
2. the qualifier is itself a food that differs **materially** from the head —
   different classification or allergens. This is Edibl's rule ("substring never
   crosses an allergen edge", its ADR-0003) and it generalises: ``cashew
   butter``, ``oat milk``, ``rice vinegar``, ``chicken stock`` and ``olive oil``
   are all refused without appearing in any list;
3. the qualifier is **functional** — if buying the canonical instead would change
   what you cook, it does not split (``self-raising flour``, ``double cream``);
Seeding a compound IS how you protect it: ``sweet potato`` and ``green onion``
are seed foods, so guard 1 keeps them whole. An earlier version also carried a
separate ``PROTECTED_COMPOUNDS`` list; removing it failed no test, because every
entry was already a seed. One list, not two.

The failure mode is therefore always "didn't normalize", never "merged two
different ingredients". An omission from the seed data costs a missed
consolidation; it cannot put the wrong thing in your basket.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --- seed vocabulary ---------------------------------------------------------
# name -> (classification, allergens). Classification and allergens are what
# guard 2 compares, so two foods that must never collapse into one another need
# to differ in at least one of them. This is data, not logic: adding a food is a
# one-line change and omitting one degrades to "not normalized".
_D = "dairy"
_N = "nuts"
_G = "gluten"

SEED_FOODS: dict[str, tuple[str, tuple[str, ...]]] = {
    # dairy — the cluster the allergen guard exists for
    "milk": ("dairy", (_D,)),
    "butter": ("dairy", (_D,)),
    "cream": ("dairy", (_D,)),
    "sour cream": ("dairy", (_D,)),
    "double cream": ("dairy", (_D,)),
    "heavy cream": ("dairy", (_D,)),
    "cream cheese": ("dairy", (_D,)),
    "condensed milk": ("dairy", (_D,)),
    "evaporated milk": ("dairy", (_D,)),
    "buttermilk": ("dairy", (_D,)),
    "yoghurt": ("dairy", (_D,)),
    "cheese": ("dairy", (_D,)),
    "parmesan": ("dairy", (_D,)),
    "egg": ("egg", ("egg",)),
    # plant milks and nut butters — same shape, different allergens on purpose
    "almond milk": ("plant milk", (_N,)),
    "oat milk": ("plant milk", (_G,)),
    "soy milk": ("plant milk", ("soy",)),
    "coconut milk": ("plant milk", ()),
    "peanut butter": ("spread", ("peanut",)),
    "almond butter": ("spread", (_N,)),
    "almond": ("nut", (_N,)),
    "cashew": ("nut", (_N,)),
    "peanut": ("legume", ("peanut",)),
    "walnut": ("nut", (_N,)),
    # The boundary guard can only refuse a qualifier it KNOWS — an unrecognised
    # one falls through as "not materially different", which is what let
    # "hazelnut butter" inherit butter's density while "walnut oil" was
    # correctly refused. The asymmetry was knowledge, not logic, so the rest of
    # the tree nuts are declared rather than the guard being made fail-closed
    # (that would wrongly refuse "sunflower oil" and "maple syrup" too).
    "hazelnut": ("nut", (_N,)),
    "pecan": ("nut", (_N,)),
    "pistachio": ("nut", (_N,)),
    "macadamia": ("nut", (_N,)),
    "pine nut": ("nut", (_N,)),
    "brazil nut": ("nut", (_N,)),
    "coconut": ("fruit", ()),
    "desiccated coconut": ("baking", ()),
    # spices — the motivating case
    "cinnamon": ("spice", ()),
    "nutmeg": ("spice", ()),
    "paprika": ("spice", ()),
    "smoked paprika": ("spice", ()),
    "cumin": ("spice", ()),
    "turmeric": ("spice", ()),
    "black pepper": ("spice", ()),
    "white pepper": ("spice", ()),
    "chilli": ("spice", ()),
    "vanilla": ("spice", ()),
    "vanilla extract": ("baking", ()),
    "bay leaf": ("herb", ()),
    "salt": ("seasoning", ()),
    # herbs
    "parsley": ("herb", ()),
    "coriander": ("herb", ()),
    "basil": ("herb", ()),
    "thyme": ("herb", ()),
    "rosemary": ("herb", ()),
    "mint": ("herb", ()),
    "dill": ("herb", ()),
    # grains, flours, baking
    "flour": ("grain", (_G,)),
    "plain flour": ("grain", (_G,)),
    "self raising flour": ("grain", (_G,)),
    "whole wheat flour": ("grain", (_G,)),
    "corn flour": ("grain", ()),
    "rice": ("grain", ()),
    "wild rice": ("grain", ()),
    "brown rice": ("grain", ()),
    "oats": ("grain", (_G,)),
    "pasta": ("grain", (_G,)),
    "orzo": ("grain", (_G,)),
    "bread": ("grain", (_G,)),
    "breadcrumbs": ("grain", (_G,)),
    "sugar": ("baking", ()),
    "brown sugar": ("baking", ()),
    "icing sugar": ("baking", ()),
    "honey": ("baking", ()),
    "baking powder": ("baking", ()),
    "baking soda": ("baking", ()),
    "cream of tartar": ("baking", ()),
    "yeast": ("baking", ()),
    "cocoa": ("baking", ()),
    "chocolate": ("baking", ()),
    "dark chocolate": ("baking", ()),
    "milk chocolate": ("baking", (_D,)),
    "white chocolate": ("baking", (_D,)),
    # fats and liquids
    "oil": ("fat", ()),
    "olive oil": ("fat", ()),
    "sesame oil": ("fat", ("sesame",)),
    "vegetable oil": ("fat", ()),
    "coconut oil": ("fat", ()),
    "vinegar": ("condiment", ()),
    "rice vinegar": ("condiment", ()),
    "balsamic vinegar": ("condiment", ()),
    "soy sauce": ("condiment", ("soy", _G)),
    "stock": ("liquid", ()),
    "chicken stock": ("liquid", ()),
    "vegetable stock": ("liquid", ()),
    "water": ("liquid", ()),
    "wine": ("alcohol", ()),
    "white wine": ("alcohol", ()),
    "red wine": ("alcohol", ()),
    # produce
    "onion": ("vegetable", ()),
    "red onion": ("vegetable", ()),
    "green onion": ("vegetable", ()),
    "garlic": ("vegetable", ()),
    "potato": ("vegetable", ()),
    "sweet potato": ("vegetable", ()),
    "tomato": ("vegetable", ()),
    "carrot": ("vegetable", ()),
    "celery": ("vegetable", ()),
    "pepper": ("vegetable", ()),
    "mushroom": ("vegetable", ()),
    "spinach": ("vegetable", ()),
    "courgette": ("vegetable", ()),
    "aubergine": ("vegetable", ()),
    "cucumber": ("vegetable", ()),
    "lettuce": ("vegetable", ()),
    "green beans": ("vegetable", ()),
    "peas": ("vegetable", ()),
    "sweet corn": ("vegetable", ()),
    "lemon": ("fruit", ()),
    "lime": ("fruit", ()),
    "orange": ("fruit", ()),
    "apple": ("fruit", ()),
    "banana": ("fruit", ()),
    "ginger": ("vegetable", ()),
    # pulses and proteins
    "black beans": ("pulse", ()),
    "red lentils": ("pulse", ()),
    "lentils": ("pulse", ()),
    "chickpeas": ("pulse", ()),
    "chicken": ("meat", ()),
    "chicken breast": ("meat", ()),
    "beef": ("meat", ()),
    "pork": ("meat", ()),
    "bacon": ("meat", ()),
    "salmon": ("fish", ("fish",)),
    "tuna": ("fish", ("fish",)),
    "prawns": ("shellfish", ("shellfish",)),
    "tofu": ("protein", ("soy",)),
    # tea/coffee
    "tea": ("drink", ()),
    "green tea": ("drink", ()),
    "coffee": ("drink", ()),
}

# Regional and spelling variants. An alias resolves to its canonical without any
# splitting — this is what makes "aubergine"/"eggplant" one food.
SEED_ALIASES: dict[str, str] = {
    "eggplant": "aubergine",
    "zucchini": "courgette",
    "cilantro": "coriander",
    "scallion": "green onion",
    "spring onion": "green onion",
    "yogurt": "yoghurt",
    "yoghourt": "yoghurt",
    "confectioners sugar": "icing sugar",
    "powdered sugar": "icing sugar",
    "all purpose flour": "plain flour",
    "cornstarch": "corn flour",
    "corn starch": "corn flour",
    "bicarbonate of soda": "baking soda",
    "baking bicarbonate": "baking soda",
    "caster sugar": "sugar",
    "granulated sugar": "sugar",
    "aubergines": "aubergine",
    "shrimp": "prawns",
    "garbanzo beans": "chickpeas",
    "coriander leaf": "coriander",
}

# Guard 3. Words that make a qualifier FUNCTIONAL: buying the canonical instead
# would change what you cook. Note these overlap with `conversions._NOISE` — that
# list is correct for a *weight* cache (salted and unsalted butter weigh the
# same) and must never be merged with this one.
FUNCTIONAL_QUALIFIERS: frozenset[str] = frozenset({
    "self raising", "selfraising", "self rising", "plain", "whole wheat",
    "wholemeal", "strong", "gluten free", "unsalted", "salted", "double",
    "heavy", "single", "sour", "clotted", "condensed", "evaporated", "skimmed",
    "semi skimmed", "dark", "white", "smoked", "desiccated", "instant",
    "quick cook", "dried", "fresh", "frozen", "raw", "cooked", "toasted",
    "low fat", "full fat", "reduced fat", "unsweetened", "sweetened",
})

# Provenance adjectives — near-universally "where it came from" rather than
# "what it is". The one generic layer the design allows itself.
NATIONALITY_QUALIFIERS: frozenset[str] = frozenset({
    "vietnamese", "ceylon", "saigon", "korean", "sichuan", "szechuan",
    "italian", "greek", "thai", "french", "spanish", "mexican", "japanese",
    "chinese", "indian", "turkish", "moroccan", "lebanese", "sicilian",
    "cornish", "scottish", "irish", "welsh", "danish", "swiss", "german",
})

_PAREN_RE = re.compile(r"\([^)]*\)")
_NONWORD_RE = re.compile(r"[^a-z0-9 ]+")
_SPACE_RE = re.compile(r"\s+")


def singularize(word: str) -> str:
    """Crude plural folding. Edibl's version only strips a trailing "s", which
    cannot reach "tomatoes" -> "tomato" or "potatoes" -> "potato"; the -oes and
    -ies forms are common enough in a recipe app to be worth the two extra rules.
    Kept compatible with Edibl for everything its rule already handled."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("oes", "ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize_text(raw: str) -> str:
    """Lowercase, drop preparation after the first comma, strip punctuation."""
    low = (raw or "").strip().lower()
    # Fold accents to ASCII rather than letting the punctuation rule turn them
    # into spaces — "jalapeño" must not become "jalape o".
    low = "".join(c for c in unicodedata.normalize("NFKD", low)
                  if not unicodedata.combining(c))
    # Everything after a comma is preparation, not identity.
    low = low.split(",")[0]
    low = _PAREN_RE.sub(" ", low)
    low = _NONWORD_RE.sub(" ", low)
    return _SPACE_RE.sub(" ", low).strip()


def fold(phrase: str) -> str:
    """A phrase with its LAST word singularized.

    Food names are head-final, so folding only the final word is enough to make
    "tomatoes" and "tomato", or "large eggs" and "large egg", meet — while
    leaving genuinely-plural seed names ("green beans", "chickpeas") intact,
    because they get folded the same way on both sides.
    """
    if not phrase:
        return phrase
    words = phrase.split()
    words[-1] = singularize(words[-1])
    return " ".join(words)


# Every seed name and alias, keyed by its folded form, so a lookup only has to
# fold the query. Built once at import; the module is pure data plus functions.
_FOLDED: dict[str, str] = {}
for _name in SEED_FOODS:
    _FOLDED.setdefault(fold(_name), _name)
for _alias, _canonical in SEED_ALIASES.items():
    _FOLDED.setdefault(fold(_alias), _canonical)


def _lookup(phrase: str) -> str | None:
    """The canonical name for a phrase, or None."""
    return _FOLDED.get(fold(phrase))


def _material(name: str) -> tuple[str, frozenset[str]] | None:
    entry = SEED_FOODS.get(name)
    return (entry[0], frozenset(entry[1])) if entry else None


def _differs_materially(qualifier: str, head: str) -> bool:
    """True when the qualifier is itself a food unlike the head food.

    This is the guard that stops "peanut butter" becoming butter and "oat milk"
    becoming milk, without either appearing in a list — the same rule Edibl
    states as "substring never crosses an allergen edge" (its ADR-0003).
    """
    qname = _lookup(qualifier)
    if not qname:
        return False
    qmat, hmat = _material(qname), _material(head)
    if not qmat or not hmat:
        return False
    return qmat[0] != hmat[0] or qmat[1] != hmat[1]


def _is_functional(qualifier: str) -> bool:
    if qualifier in FUNCTIONAL_QUALIFIERS:
        return True
    # Multi-word qualifiers are checked as phrases too, so "extra virgin" is not
    # shredded into "extra" + "virgin" — the same longest-first idea as _UNIT_KEYS.
    return any(word in FUNCTIONAL_QUALIFIERS for word in qualifier.split())


# Longest first, so "olive oil" is tried before "oil" and "black pepper" before
# "pepper". Without this, "extra virgin olive oil" resolves to plain oil.
_SEED_BY_LENGTH = sorted(SEED_FOODS, key=lambda n: (-len(n), n))


def normalize(raw: str) -> tuple[str, str, str]:
    """``(food, qualifier, matched_on)`` for a free-text ingredient name.

    ``food`` is the canonical name, ``qualifier`` the variety (possibly ""), and
    ``matched_on`` says which rule fired — ``exact``, ``alias``, ``split``,
    ``protected``, ``material``, ``functional`` or ``unknown``. Callers surface
    it, and the tests assert on it so a case cannot pass for the wrong reason.

    Never raises, and never returns an empty food for non-empty input: an
    unrecognised phrase comes back unchanged, which is the safe outcome.
    """
    text = normalize_text(raw)
    if not text:
        return "", "", "unknown"

    # Guard 1 — the whole phrase is itself a food.
    direct = _lookup(text)
    if direct:
        return direct, "", ("exact" if fold(direct) == fold(text) else "alias")

    folded = fold(text)
    for head in _SEED_BY_LENGTH:
        suffix = " " + fold(head)
        if not folded.endswith(suffix):
            continue
        qualifier = folded[: -len(suffix)].strip()
        if not qualifier:
            continue
        # Guard 2 — the qualifier is a materially different food.
        if _differs_materially(qualifier, head):
            return text, "", "material"
        # Guard 3 — the qualifier changes what you cook.
        if _is_functional(qualifier):
            return text, "", "functional"
        return head, qualifier, "split"

    return text, "", "unknown"


# Words describing preparation or grade — never identity. Stripping them is
# what makes "unsalted butter, softened" and "butter" share one weight answer,
# which is correct BECAUSE THEY WEIGH THE SAME.
#
# ⚠️ This list is emphatically NOT the shopping-list qualifier lexicon and must
# never be used as one. "plain" belongs here (plain and self-raising flour have
# the same density) and must NOT split for shopping (buying the wrong one ruins
# a bake). Two lists that look alike with opposite safety properties; that is
# why they live apart with this comment between them. See docs/adr/0001.
PREPARATION_WORDS = frozenset({
    "fresh", "freshly", "dried", "chopped", "finely", "roughly", "large",
    "small", "medium", "unsalted", "salted", "ripe", "raw", "cooked", "of",
    "a", "an", "the", "plain", "whole", "ground", "grated", "melted", "softened",
})


def is_known(name: str) -> bool:
    """True when the lexicon actually recognises this food.

    The difference between "we canonicalised this" and "we left it alone":
    match_key returns the input unchanged for an unrecognised phrase, which is
    the right key but the wrong thing to SHOW. Rewriting a display name we do
    not understand mangles it — "bone-in chicken thighs" came back as "bone in
    chicken thighs" because normalisation strips punctuation.
    """
    return _lookup(name) is not None


def match_key(raw: str) -> str:
    """The canonical key for asking "are these two texts the same food?".

    Preparation words come off first, then the normal canonicaliser — so
    "unsalted butter, softened" and "butter" agree while "peanut butter" and
    "butter" stay apart.

    Used by three callers that must agree or they contradict each other in front
    of the user: the learned-weight cache key, the density lookup, and inventory
    coverage. Preparation and grade are irrelevant to all three — a chopped
    onion weighs what an onion weighs, and having onions means you have chopped
    onions.
    """
    text = normalize_text(raw)
    if not text:
        return ""
    words = [w for w in text.split() if w not in PREPARATION_WORDS]
    return food_key(" ".join(words)) if words else ""


def food_key(raw: str) -> str:
    """The canonical name alone — the cache/consolidation key.

    Replaces ``conversions.food_term``'s last-word rule, which mapped "peanut
    butter" to "butter" and let a learned "1 stick butter = 113 g" be applied to
    peanut butter.
    """
    return normalize(raw)[0]


# --- the database-backed half ------------------------------------------------
# Everything above is pure. What follows ranks a name against a household's OWN
# Food rows, and is modelled directly on Edibl's services/matching.py: ranked
# candidates carrying a score and the reason they matched, with a separate
# "resolve for a mutation" gate that refuses to guess.

# Score tiers. The gaps are deliberately wide so a stronger KIND of evidence
# always beats a pile of weaker ones — the same doctrine as recipe_resolve and
# Edibl's matcher. MEANINGFUL is the floor for "real evidence the user meant
# this food"; a description-only hit sits below it and can never, on its own,
# pick between two foods.
SCORE_EXACT = 1.0
SCORE_ALIAS = 0.9
SCORE_SPLIT = 0.8        # "Vietnamese cinnamon" → an existing `cinnamon` row
SCORE_SUBSTRING = 0.5
MEANINGFUL = SCORE_SUBSTRING
SCORE_DESCRIPTION = 0.2


_MIN_SUBSTRING = 3


def _contains(query: str, name: str) -> bool:
    """Substring evidence in either direction, with a length floor on both.

    Without the floor a Food called "ox" matches almost every query, which is
    the exact bug Edibl's matcher carries a comment about.
    """
    if len(name) >= _MIN_SUBSTRING and name in query:
        return True
    return len(query) >= _MIN_SUBSTRING and query in name


@dataclass
class Candidate:
    food: object
    score: float
    reasons: list = field(default_factory=list)


@dataclass
class Resolution:
    food: object            # the resolved Food, or None
    qualifier: str          # the variety text left over, or ""
    ambiguous: bool
    candidates: list


def _food_terms(food) -> list[str]:
    """Every name this Food answers to, normalized: its own plus its aliases."""
    raw = [getattr(food, "name", "")] + aliases_of(food)
    return [fold(normalize_text(t)) for t in raw if t]


def aliases_of(food) -> list[str]:
    """Food.aliases as a list, whatever it is stored as.

    It is a JSON list after migration 0014 and a comma-separated string on rows
    from before it (or in test fakes); three different call sites used to each
    split it their own way. This is the only place that knows.
    """
    value = getattr(food, "aliases", None)
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


# --- the alias write policy --------------------------------------------------
# One folded key resolves to at most ONE food per household. That invariant is
# what makes alias-driven resolution deterministic, and it lives here — at the
# write chokepoint, where names AND aliases are both visible — rather than as a
# DB constraint, which could only see aliases (names live on the foods table).
# A relational food_aliases table was evaluated and deliberately rejected at
# this scale; the promotion triggers are recorded in docs/adr/0001.

# A count cap, replacing the old 500-BYTE cap whose truncation silently dropped
# later merges' names — after which a re-import recreated exactly the row that
# had been merged away.
MAX_ALIASES = 50


def alias_key(term: str) -> str:
    """THE alias equivalence: folded, singularized, comma-truncated.

    One definition, used by every alias write and lookup. Before this,
    create/update compared raw lowercase while the ranker compared folded keys
    — two disagreeing answers to "is this the same name?", so "Cilantros"
    could be created as a duplicate the ranker then scored as the same food.

    Deliberately fold(normalize_text()), NOT normalize()/match_key: the key
    does not canonicalise, so "Vietnamese cinnamon" keys apart from "cinnamon"
    and a variety stays creatable as its own food.
    """
    return fold(normalize_text(term or ""))


def clean_alias(term: str) -> str:
    """One alias as stored: its identity part, whitespace-collapsed.

    Everything after a comma is cut because ``normalize_text`` cuts it — the
    resolver can only ever see the pre-comma part, and storing text the
    resolver silently discards is how "salt, kosher" hid a wrong resolution
    behind a correct-looking stored list.
    """
    head = str(term or "").split(",")[0]
    return " ".join(head.split())[:255]


def claim_index(gid: str) -> dict:
    """Every folded key this household's foods answer to -> the owning food.

    One query. Iteration order is (created_at, id), so when legacy data
    already contains a collision the winner is deterministic across requests —
    not whatever row order the database happens to return.
    """
    from ..extensions import db
    from ..models import Food

    claims: dict[str, object] = {}
    foods = (db.session.query(Food).filter_by(group_id=gid)
             .order_by(Food.created_at, Food.id).all())
    # Names claim first, in one full pass: a name always outranks an alias,
    # even an alias belonging to an earlier-created food.
    for food in foods:
        key = alias_key(food.name)
        if key:
            claims.setdefault(key, food)
    for food in foods:
        for term in aliases_of(food):
            key = alias_key(term)
            if key:
                claims.setdefault(key, food)
    return claims


def set_aliases(food, terms, gid: str) -> list[str]:
    """Apply an alias list to a food under the one-key-one-food invariant.

    Dedupes by key, drops a key equal to the food's own name, and REFUSES any
    key already claimed by a different food in the household (by name or
    alias). Refusal degrades to "not consolidated", never to two foods
    answering to one word. Returns what was applied, so a caller can surface
    what was refused.
    """
    claims = claim_index(gid)
    own_key = alias_key(getattr(food, "name", ""))
    own_canonical = _lookup(normalize_text(getattr(food, "name", "")))
    kept, seen = [], set()
    for term in terms:
        term = clean_alias(term)
        key = alias_key(term)
        if not term or not key or key == own_key or key in seen:
            continue
        owner = claims.get(key)
        if owner is not None and owner.id != getattr(food, "id", None):
            continue  # claimed elsewhere in the household — refuse, don't steal
        # The lexicon guard: a term the SEED lexicon knows as a DIFFERENT food
        # cannot become an alias — "salt" must never alias cinnamon, even when
        # no household row claims it, because the ranker would then answer a
        # real food word with the wrong ingredient. Same canonical is fine
        # (that is what an alias IS: "cilantro" for a food named "coriander");
        # unknown terms are fine (the household's own vocabulary).
        canonical = _lookup(normalize_text(term))
        if canonical and canonical != own_canonical:
            continue
        seen.add(key)
        kept.append(term)
        if len(kept) >= MAX_ALIASES:
            break
    food.aliases = kept
    return kept


def index(gid: str):
    """A memoising matcher over one household's foods.

    Two separate savings, worth keeping straight:

    * the household's foods are loaded ONCE, which is what prevents an N+1 —
      `_set_ingredients` handles up to 200 rows and the shopping build touches
      every leaf ingredient of a two-week plan;
    * repeated lookups of the same name are memoised, which saves the ranking
      work (every food × the seed vocabulary), not a query.

    Same shape as ``conversions.resolver``.
    """
    from ..models import Food
    from ..extensions import db

    foods = db.session.query(Food).filter_by(group_id=gid).all()
    cache: dict[str, Resolution] = {}

    def match(raw: str) -> Resolution:
        key = fold(normalize_text(raw))
        if key not in cache:
            cache[key] = _rank(foods, raw)
        return cache[key]

    match.foods = foods          # exposed for callers that need the raw list
    return match


def _rank(foods, raw: str) -> Resolution:
    """Rank a free-text name against these foods. Never raises."""
    text = normalize_text(raw)
    if not text:
        return Resolution(None, "", False, [])
    folded = fold(text)
    canonical, qualifier, _why = normalize(raw)
    canonical_folded = fold(normalize_text(canonical))

    out: list[Candidate] = []
    for food in foods:
        terms = _food_terms(food)
        if not terms:
            continue
        name_term = terms[0]
        score, reasons = 0.0, []
        if folded == name_term:
            score, reasons = SCORE_EXACT, ["exact name"]
        elif folded in terms[1:]:
            score, reasons = SCORE_ALIAS, ["alias"]
        elif canonical_folded and canonical_folded in terms:
            # The pure layer already decided this split is safe — it cleared the
            # material-boundary and functional-qualifier guards — so a household
            # Food matching the canonical is strong evidence, not a substring.
            score, reasons = SCORE_SPLIT, ["variety of"]
        elif _contains(folded, name_term):
            # BOTH directions, as in Edibl: a longer phrase finds a shorter food
            # ("2 sticks butter" → `butter`) and a shorter query finds longer
            # foods ("pepper" → `red pepper`, `green pepper` — which is then a
            # question for a write, not an answer). Length-floored so a
            # two-letter row cannot match almost everything.
            score, reasons = SCORE_SUBSTRING, ["substring"]
        elif folded and folded in fold(normalize_text(getattr(food, "description", "") or "")):
            score, reasons = SCORE_DESCRIPTION, ["description"]
        if score:
            out.append(Candidate(food=food, score=score, reasons=reasons))

    out.sort(key=lambda c: (-c.score, (getattr(c.food, "name", "") or "").lower()))
    top = out[0] if out else None
    resolved_qualifier = qualifier if (top and "variety of" in top.reasons) else ""
    return Resolution(top.food if top else None, resolved_qualifier, False, out)


def resolve_for_mutation(match, raw: str) -> Resolution:
    """Pick ONE food for a WRITE, or refuse.

    Writing a food_id is a mutation, and this codebase's rule (Edibl ADR-0003,
    and now docs/adr/0001) is that mutations do not guess. Weak-only evidence
    never picks between candidates, and a near-tie is a question rather than an
    answer — the caller leaves food_id NULL and shows the candidates instead.
    """
    res = match(raw)
    cands = res.candidates
    if not cands:
        return Resolution(None, "", False, [])
    top = cands[0]

    if top.score < MEANINGFUL:
        # A description-only hit resolves when it is the ONLY thing found, and
        # is a question when several foods mention the words.
        if len(cands) == 1:
            return Resolution(top.food, res.qualifier, False, cands)
        return Resolution(None, "", True, cands)

    rivals = [c for c in cands[1:] if c.score >= MEANINGFUL]
    runner = rivals[0].score if rivals else 0.0
    if top.score - runner >= 0.3 or not rivals:
        return Resolution(top.food, res.qualifier, False, cands)
    return Resolution(None, "", True, cands)
