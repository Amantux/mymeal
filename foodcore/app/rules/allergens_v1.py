"""Allergen rule data, version 1 — DATA ONLY, no logic.

Everything here is a lookup table. The matcher in ``app/allergens.py`` contains
no allergen names and no food names; adding an alias is a one-line change to
this file and bumps ``VERSION``, which every piece of evidence records.

Four things make this data safe rather than merely long:

1. **Longest phrase wins, and a matched phrase consumes its tokens.** That is
   how the material-boundary rule of ``docs/adr/0001`` is enforced without a
   separate guard: ``almond milk`` is a longer entry than ``milk``, so it
   matches first, consumes both tokens, and ``milk`` can never fire. Any
   compound whose head word belongs to another material MUST be listed in
   :data:`MATERIAL_PHRASES`.
2. **Unlisted compounds fail toward over-restriction.** "tiger nut milk" is not
   in the table, so ``milk`` matches and the recipe is excluded from a
   milk-avoiding search. That is the opposite failure direction from
   ``food_resolve.normalize`` (which prefers "didn't normalise"), and it is the
   correct direction here: a missing alias costs a false exclusion, never a
   false clearance.
3. **Ambiguity is data too.** :data:`MAY_CONTAIN_TERMS` holds the things that
   usually but not always carry an allergen (``noodles`` → wheat, ``lecithin``
   → soy, ``oats`` → gluten by cross-contact). They can never produce
   ``contains``, and they can never produce a clearance either.
4. **Opaque things are named.** :data:`OPAQUE_TERMS` are composites whose
   composition we do not have ("sauce", "seasoning blend"). They force
   ``unknown`` rather than letting a recognised head word imply we screened the
   whole product.

Coconut is listed under tree nuts deliberately: the FDA's labelling guidance
counts it as one even though allergists frequently do not. Following the
regulatory list over-restricts, which is the safe direction, and the rule note
says so, so a UI can explain it.
"""
from __future__ import annotations

VERSION = "allergens/2026.10.1"
LICENSE = "CC0-1.0"
# When this rule data was last reviewed against its sources. Recorded on every
# piece of evidence as the provenance `retrieved` field.
RETRIEVED = "2026-10-16"
SOURCE = "foodcore.rules.allergens_v1"

# --- allergen catalogue ------------------------------------------------------
# key -> (label, is_us_major_allergen, groups this key also implies)
ALLERGENS: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    # The nine US major allergens (FALCPA + the FASTER Act's sesame).
    "milk": ("Milk", True, ()),
    "egg": ("Egg", True, ()),
    "fish": ("Fish", True, ()),
    "crustacean_shellfish": ("Crustacean shellfish", True, ()),
    "tree_nuts": ("Tree nuts", True, ()),
    "peanuts": ("Peanuts", True, ()),
    "wheat": ("Wheat", True, ("gluten",)),
    "soybeans": ("Soybeans", True, ()),
    "sesame": ("Sesame", True, ()),
    # Configurable sensitivity profiles — not US majors, commonly avoided.
    "gluten": ("Gluten", False, ()),
    "sulfites": ("Sulfites", False, ()),
    "molluscs": ("Molluscs", False, ()),
}

# allergen -> match_kind -> terms. The kind is recorded in the evidence trail so
# a user can see WHY: a canonical name, a regional alias, or a derived
# substance whose name does not contain the allergen's own name at all.
TERMS: dict[str, dict[str, tuple[str, ...]]] = {
    "milk": {
        "canonical": ("milk", "dairy"),
        "alias": ("buttermilk", "skimmed milk", "whole milk", "semi skimmed milk"),
        "derivative": (
            "butter", "butterfat", "butter oil", "ghee", "cream", "creme fraiche",
            "cheese", "parmesan", "parmigiano reggiano", "pecorino", "cheddar",
            "mozzarella", "feta", "ricotta", "mascarpone", "halloumi", "paneer",
            "gruyere", "emmental", "brie", "camembert", "stilton", "gorgonzola",
            "yoghurt", "yogurt", "kefir", "quark", "curd", "casein", "caseinate",
            "sodium caseinate", "calcium caseinate", "whey", "whey protein",
            "whey powder", "lactose", "lactalbumin", "lactoglobulin",
            "milk powder", "milk solids", "condensed milk", "evaporated milk",
            "custard", "creme anglaise", "dulce de leche", "ice cream",
            "milk chocolate", "white chocolate", "clotted cream", "sour cream",
            "double cream", "heavy cream", "single cream", "whipping cream",
            "creme patissiere", "bechamel", "mornay sauce",
        ),
    },
    "egg": {
        "canonical": ("egg", "eggs"),
        "alias": ("egg white", "egg whites", "egg yolk", "egg yolks", "egg powder"),
        "derivative": (
            "albumen", "albumin", "ovalbumin", "globulin", "lysozyme",
            "mayonnaise", "mayo", "aioli", "meringue", "hollandaise",
            "egg wash", "dried egg", "egg noodles", "frittata",
        ),
    },
    "fish": {
        "canonical": ("fish",),
        "alias": (
            "anchovy", "anchovies", "sardine", "sardines", "salmon", "tuna",
            "cod", "haddock", "mackerel", "trout", "sea bass", "pollock",
            "tilapia", "halibut", "sole", "snapper", "hake", "herring",
            "kipper", "whitebait", "monkfish", "plaice", "eel",
        ),
        "derivative": (
            "fish sauce", "nam pla", "nuoc mam", "worcestershire sauce",
            "caviar", "roe", "bottarga", "bonito", "katsuobushi", "dashi",
            "isinglass", "fish stock", "garum", "colatura", "surimi",
            "crab stick", "crab sticks", "gentleman's relish",
        ),
    },
    "crustacean_shellfish": {
        "canonical": ("crustacean", "crustaceans", "shellfish"),
        "alias": (
            "shrimp", "shrimps", "prawn", "prawns", "king prawn", "king prawns",
            "crab", "lobster", "langoustine", "langoustines", "crayfish",
            "crawfish", "krill", "scampi",
        ),
        "derivative": ("shrimp paste", "belacan", "crab paste", "lobster bisque", "crab meat"),
    },
    "molluscs": {
        "canonical": ("mollusc", "molluscs", "mollusk", "mollusks"),
        "alias": (
            "mussel", "mussels", "clam", "clams", "oyster", "oysters",
            "squid", "calamari", "octopus", "scallop", "scallops", "cockle",
            "cockles", "whelk", "winkle", "abalone", "snail", "escargot",
        ),
        "derivative": ("oyster sauce", "squid ink", "cuttlefish ink"),
    },
    "tree_nuts": {
        "canonical": ("tree nut", "tree nuts", "nut", "nuts"),
        "alias": (
            "almond", "almonds", "hazelnut", "hazelnuts", "filbert", "walnut",
            "walnuts", "pecan", "pecans", "pistachio", "pistachios", "cashew",
            "cashews", "macadamia", "macadamias", "brazil nut", "brazil nuts",
            "pine nut", "pine nuts", "pignoli", "chestnut", "chestnuts",
            "mixed nuts",
        ),
        "derivative": (
            "marzipan", "frangipane", "praline", "nougat", "gianduja",
            "nutella", "amaretti", "amaretto", "almond extract", "almond essence",
            "almond flour", "almond meal", "ground almonds", "flaked almonds",
            "nut butter", "pesto", "baklava", "nut oil", "walnut oil",
            "hazelnut oil", "almond oil", "macaroon",
            # Regulatory inclusion, not a botanical one — see the module
            # docstring. Over-restricting is the safe direction.
            "coconut", "desiccated coconut", "coconut flakes",
        ),
    },
    "peanuts": {
        "canonical": ("peanut", "peanuts"),
        "alias": ("groundnut", "groundnuts", "monkey nut", "monkey nuts", "beer nuts"),
        "derivative": (
            "peanut butter", "peanut oil", "arachis", "arachis oil",
            "satay", "satay sauce", "peanut flour", "pb powder",
        ),
    },
    "wheat": {
        "canonical": ("wheat", "wheat flour", "whole wheat", "wholewheat"),
        "alias": (
            "semolina", "durum", "durum wheat", "farina", "spelt", "kamut",
            "einkorn", "emmer", "bulgur", "bulghur", "freekeh", "farro",
            "couscous", "matzo", "matzah", "triticale",
        ),
        "derivative": (
            "flour", "plain flour", "all purpose flour", "bread flour",
            "strong flour", "cake flour", "self raising flour",
            "self rising flour", "wholemeal flour", "bread", "breadcrumbs",
            "bread crumbs", "panko", "croutons", "pasta", "spaghetti",
            "macaroni", "penne", "fusilli", "tagliatelle", "lasagne",
            "lasagna sheets", "orzo", "vermicelli", "seitan", "wheat gluten",
            "vital wheat gluten", "wheat starch", "wheat bran", "wheat germ",
            "cracker", "crackers", "biscuit", "biscuits", "pastry",
            "puff pastry", "shortcrust pastry", "filo", "phyllo", "pita",
            "pitta", "naan", "brioche", "ciabatta", "focaccia", "baguette",
            "pretzel", "pretzels", "roux", "pie crust", "digestive biscuits",
            "yufka", "udon", "soy sauce", "teriyaki sauce", "hoisin sauce",
        ),
    },
    "soybeans": {
        "canonical": ("soy", "soya", "soybean", "soybeans", "soya bean", "soya beans"),
        "alias": ("edamame", "yuba", "okara"),
        "derivative": (
            "soy sauce", "shoyu", "tamari", "tofu", "bean curd", "tempeh",
            "miso", "miso paste", "natto", "soy protein", "soya protein",
            "textured vegetable protein", "tvp", "soy lecithin",
            "soya lecithin", "teriyaki sauce", "hoisin sauce", "ponzu",
            "soy flour", "soya flour",
        ),
    },
    "sesame": {
        "canonical": ("sesame", "sesame seed", "sesame seeds", "simsim", "benne"),
        "alias": ("gingelly", "til"),
        "derivative": (
            "tahini", "tahina", "tahin", "sesame oil", "toasted sesame oil",
            "halva", "halvah", "gomashio", "gomasio", "zaatar", "za atar",
            "hummus", "houmous", "baba ganoush", "furikake", "sesame paste",
            "burger bun", "burger buns",
        ),
    },
    "gluten": {
        "canonical": ("gluten",),
        "alias": ("barley", "rye", "pumpernickel", "malt", "malt extract",
                  "malted barley", "malt vinegar", "barley malt"),
        "derivative": ("beer", "ale", "stout", "lager", "brewers yeast",
                       "brewer's yeast", "seitan", "rye bread", "barley flour"),
    },
    "sulfites": {
        "canonical": ("sulfite", "sulfites", "sulphite", "sulphites"),
        "alias": ("sulfur dioxide", "sulphur dioxide", "e220", "e221", "e222",
                  "e223", "e224", "e226", "e227", "e228"),
        "derivative": ("sodium metabisulfite", "potassium metabisulfite",
                       "sodium metabisulphite", "sodium bisulfite"),
    },
}

# Terms that USUALLY but not always carry the allergen. These can only ever
# produce `may_contain`: never a `contains`, and never a clearance.
MAY_CONTAIN_TERMS: dict[str, tuple[tuple[str, str], ...]] = {
    "wheat": (
        ("noodles", "noodles may be wheat, rice or buckwheat"),
        ("tortilla", "tortillas may be wheat or corn"),
        ("tortillas", "tortillas may be wheat or corn"),
        ("stuffing", "stuffing is usually bread-based"),
        ("tamari", "tamari is usually but not always wheat-free"),
        ("baking powder", "some baking powders use wheat starch as the filler"),
        ("oats", "oats are routinely cross-contacted with wheat"),
    ),
    "gluten": (
        ("oats", "oats are gluten-free botanically but routinely cross-contacted"),
        ("oat milk", "made from oats; cross-contact risk unless certified"),
        ("oat flour", "made from oats; cross-contact risk unless certified"),
        ("porridge oats", "cross-contact risk unless certified"),
        ("rolled oats", "cross-contact risk unless certified"),
        ("noodles", "noodles may be wheat, rice or buckwheat"),
    ),
    "soybeans": (
        ("lecithin", "lecithin is most commonly soy-derived"),
        ("vegetable oil", "unspecified vegetable oil may be soybean oil"),
        ("vegetable protein", "unspecified vegetable protein is often soy"),
        ("natural flavouring", "undeclared carriers may be soy-based"),
        ("natural flavoring", "undeclared carriers may be soy-based"),
    ),
    "milk": (
        ("chocolate", "plain chocolate is commonly made on milk-shared lines"),
        ("dark chocolate", "commonly made on milk-shared lines"),
        ("chocolate chips", "commonly made on milk-shared lines"),
        ("caramel", "caramel is usually but not always dairy-based"),
        ("nougat", "many recipes include milk powder"),
        ("pesto", "most jarred pesto contains hard cheese"),
    ),
    "tree_nuts": (
        ("pesto", "most pesto contains pine nuts"),
        ("praline", "usually hazelnut or almond"),
        ("granola", "granola commonly contains nuts"),
        ("muesli", "muesli commonly contains nuts"),
    ),
    "fish": (
        ("caesar dressing", "classic Caesar dressing contains anchovy"),
        ("caesar salad dressing", "classic Caesar dressing contains anchovy"),
        ("kimchi", "many kimchi recipes include fish sauce or shrimp"),
    ),
    "crustacean_shellfish": (
        ("surimi", "surimi is fish, but is routinely crab-flavoured and co-processed"),
        ("kimchi", "many kimchi recipes include salted shrimp"),
        ("xo sauce", "contains dried shrimp"),
    ),
    "egg": (
        ("pasta", "fresh pasta is usually egg-based; dried pasta usually is not"),
        ("fresh pasta", "usually egg-based"),
        ("brioche", "enriched doughs are usually egg-washed or egg-enriched"),
    ),
    "sesame": (
        ("burger bun", "seeded buns are common and seeds are often undeclared on menus"),
        ("falafel", "frequently served with tahini"),
    ),
    "sulfites": (
        ("wine", "wine almost always contains added or natural sulfites"),
        ("white wine", "sulfites are near-universal"),
        ("red wine", "sulfites are near-universal"),
        ("balsamic vinegar", "commonly sulfited"),
        ("dried apricots", "commonly sulfited"),
        ("dried fruit", "commonly sulfited"),
        ("cider", "commonly sulfited"),
    ),
}

# --- the material-boundary table (ADR-0001 guard 2) -------------------------
# Compounds whose head word belongs to a DIFFERENT material. Each entry states
# the allergens the compound really carries, which may be none. These are
# longer than the bare head word, so longest-match-first consumption means the
# head word never gets a chance to fire. "almond milk" must never read as milk.
#
# `()` means "recognised, and carries no allergen we know of" — which is
# evidence of screening, NOT a safety claim; see allergens.py.
MATERIAL_PHRASES: dict[str, tuple[tuple[str, str], ...]] = {
    # plant milks — the motivating case for the whole table
    "almond milk": (("tree_nuts", "contains"),),
    "cashew milk": (("tree_nuts", "contains"),),
    "hazelnut milk": (("tree_nuts", "contains"),),
    "walnut milk": (("tree_nuts", "contains"),),
    "coconut milk": (("tree_nuts", "contains"),),
    "coconut cream": (("tree_nuts", "contains"),),
    "coconut yoghurt": (("tree_nuts", "contains"),),
    "coconut yogurt": (("tree_nuts", "contains"),),
    "soy milk": (("soybeans", "contains"),),
    "soya milk": (("soybeans", "contains"),),
    "oat milk": (("gluten", "may_contain"),),
    "rice milk": (),
    "hemp milk": (),
    "flax milk": (),
    "pea milk": (),
    "potato milk": (),
    "tiger nut milk": (),
    "nut milk": (("tree_nuts", "contains"),),
    "plant milk": (),
    # butters that are not butter
    "peanut butter": (("peanuts", "contains"),),
    "almond butter": (("tree_nuts", "contains"),),
    "cashew butter": (("tree_nuts", "contains"),),
    "hazelnut butter": (("tree_nuts", "contains"),),
    "cocoa butter": (),
    "cacao butter": (),
    "shea butter": (),
    "apple butter": (),
    "sunflower seed butter": (),
    "sunflower butter": (),
    "coconut butter": (("tree_nuts", "contains"),),
    "coconut oil": (),
    # flours that are not wheat
    "rice flour": (),
    "corn flour": (),
    "cornflour": (),
    "corn starch": (),
    "cornstarch": (),
    "potato flour": (),
    "potato starch": (),
    "tapioca flour": (),
    "tapioca starch": (),
    "chickpea flour": (),
    "gram flour": (),
    "buckwheat flour": (),
    "buckwheat": (),
    "almond flour": (("tree_nuts", "contains"),),
    "coconut flour": (("tree_nuts", "contains"),),
    "soy flour": (("soybeans", "contains"),),
    "oat flour": (("gluten", "may_contain"),),
    # things that merely sound like an allergen
    "egg plant": (),
    "eggplant": (),
    "aubergine": (),
    "butternut squash": (),
    "butternut": (),
    "water chestnut": (),
    "water chestnuts": (),
    "nutmeg": (),
    "milk thistle": (),
    "coconut aminos": (),
    "nutritional yeast": (),
    "cream of tartar": (),
    "creme de menthe": (),
    # grains that are not gluten grains
    "rice noodles": (),
    "glass noodles": (),
    "soba noodles": (("wheat", "may_contain"), ("gluten", "may_contain")),
    "rice vinegar": (),
    "quinoa": (),
    "millet": (),
    "sorghum": (),
    "amaranth": (),
    "teff": (),
    "polenta": (),
    "corn tortilla": (),
    "corn tortillas": (),
}

# --- declared free-from claims ----------------------------------------------
# "<claim> free" in an ingredient's own text is a DECLARED claim. We respect it
# by downgrading an inferred `contains` to `may_contain` — never to a clearance,
# because we did not verify the label. Under a strict profile the ingredient is
# still excluded; under a preference profile it passes with the claim shown.
FREE_FROM_CLAIMS: dict[str, tuple[str, ...]] = {
    "gluten": ("gluten", "wheat"),
    "wheat": ("wheat",),
    "dairy": ("milk",),
    "milk": ("milk",),
    "lactose": ("milk",),
    "egg": ("egg",),
    "nut": ("tree_nuts", "peanuts"),
    "nuts": ("tree_nuts", "peanuts"),
    "peanut": ("peanuts",),
    "soy": ("soybeans",),
    "soya": ("soybeans",),
    "sesame": ("sesame",),
    "fish": ("fish",),
    "shellfish": ("crustacean_shellfish", "molluscs"),
    "sulfite": ("sulfites",),
    "sulphite": ("sulfites",),
    "allergen": tuple(ALLERGENS),
}

# A "vegan" claim on a product is a declared claim about animal ingredients.
# Same treatment as a free-from claim: downgrade, never clear.
VEGAN_CLAIM_ALLERGENS: tuple[str, ...] = (
    "milk", "egg", "fish", "crustacean_shellfish", "molluscs",
)

# --- precaution and opacity --------------------------------------------------
# Anything after one of these markers is a PRECAUTIONARY statement: matches in
# that segment can only produce `may_contain`.
PRECAUTION_MARKERS: tuple[str, ...] = (
    "may contain", "may also contain", "might contain", "traces of",
    "trace of", "trace amounts", "produced in a facility",
    "made in a facility", "packed in a facility", "shared equipment",
    "manufactured on equipment", "processed in a plant", "not suitable for",
    "cannot guarantee",
)

# Composites whose composition we do not hold. Recognising the head word is NOT
# screening the product, so these force `unknown` for every allergen that has
# no stronger evidence. The hidden-allergen substitution case lives here.
OPAQUE_TERMS: tuple[str, ...] = (
    "sauce", "sauces", "seasoning", "seasoning blend", "spice blend",
    "spice mix", "curry paste", "curry powder", "marinade", "dressing",
    "gravy", "gravy granules", "bouillon", "stock cube", "stock cubes",
    "stock pot", "broth concentrate", "flavouring", "flavoring",
    "natural flavouring", "natural flavoring", "spread", "margarine",
    "shortening", "mix", "blend", "sprinkles", "icing", "frosting",
    "protein powder", "meal replacement", "energy bar", "cereal bar",
    "granola", "muesli", "ready meal", "pastry case", "puff pastry sheet",
    "vegan cheese", "vegan butter", "plant based butter", "plant butter",
    "meat substitute", "plant based mince", "veggie burger", "burger",
    "sausage", "sausages", "stock", "broth", "hot sauce", "bbq sauce",
    "barbecue sauce", "worcestershire", "liquid smoke", "bitters",
)

# Recognised and carrying no allergen we know of. Needed so that a plain,
# fully-recognised recipe can reach `no_known_ingredient_identified` instead of
# every ingredient reading as unknown. Deliberately conservative: a single-
# ingredient whole food, or a compound already covered by MATERIAL_PHRASES.
KNOWN_FREE: tuple[str, ...] = (
    # vegetables and fruit
    "onion", "onions", "red onion", "white onion", "spring onion",
    "green onion", "scallion", "scallions", "shallot", "shallots", "garlic",
    "ginger", "potato", "potatoes", "sweet potato", "sweet potatoes",
    "tomato", "tomatoes", "cherry tomatoes", "tinned tomatoes",
    "chopped tomatoes", "tomato puree", "tomato paste", "passata", "carrot",
    "carrots", "celery", "celeriac", "pepper", "peppers", "red pepper",
    "green pepper", "yellow pepper", "bell pepper", "chilli", "chillies",
    "chili", "jalapeno", "mushroom", "mushrooms", "spinach", "kale",
    "cabbage", "broccoli", "cauliflower", "courgette", "courgettes",
    "zucchini", "cucumber", "lettuce", "rocket", "arugula", "watercress",
    "leek", "leeks", "parsnip", "parsnips", "turnip", "swede", "beetroot",
    "pumpkin", "squash", "green beans", "runner beans", "peas", "sugar snap peas",
    "mangetout", "sweetcorn", "sweet corn", "corn", "corn on the cob",
    "asparagus", "artichoke", "fennel", "radish", "radishes", "pak choi",
    "bok choy", "avocado", "olive", "olives", "caper", "capers", "samphire",
    "lemon", "lemons", "lemon juice", "lemon zest", "lime", "limes",
    "lime juice", "orange", "oranges", "orange juice", "apple", "apples",
    "pear", "pears", "banana", "bananas", "strawberry", "strawberries",
    "raspberry", "raspberries", "blueberry", "blueberries", "blackberry",
    "blackberries", "cherry", "cherries", "grape", "grapes", "peach",
    "peaches", "nectarine", "plum", "plums", "mango", "pineapple",
    "watermelon", "melon", "kiwi", "pomegranate", "fig", "figs", "date",
    "dates", "raisin", "raisins", "sultana", "sultanas", "currants",
    "cranberry", "cranberries", "rhubarb",
    # meat and poultry
    "chicken", "chicken breast", "chicken breasts", "chicken thigh",
    "chicken thighs", "chicken wings", "whole chicken", "beef", "beef mince",
    "minced beef", "ground beef", "steak", "braising steak", "brisket",
    "pork", "pork belly", "pork chop", "pork chops", "pork mince", "bacon",
    "pancetta", "ham", "gammon", "lamb", "lamb mince", "leg of lamb", "mutton",
    "turkey", "turkey mince", "duck", "duck breast", "goose", "venison",
    "rabbit", "chorizo", "prosciutto", "guanciale", "ox cheek", "oxtail",
    "liver", "kidney", "mince",
    # grains, pulses, seeds
    "rice", "white rice", "brown rice", "basmati rice", "jasmine rice",
    "arborio rice", "risotto rice", "wild rice", "lentil", "lentils",
    "red lentils", "green lentils", "puy lentils", "chickpea", "chickpeas",
    "black beans", "kidney beans", "cannellini beans", "butter beans",
    "borlotti beans", "haricot beans", "broad beans", "split peas",
    "sunflower seeds", "pumpkin seeds", "poppy seeds", "chia seeds",
    "flaxseed", "linseed", "hemp seeds", "nigella seeds",
    # fats, seasonings, baking, drinks
    "olive oil", "extra virgin olive oil", "rapeseed oil", "canola oil",
    "sunflower oil", "avocado oil", "salt", "sea salt",
    "table salt", "black pepper", "white pepper", "paprika", "smoked paprika",
    "cumin", "coriander", "cilantro", "turmeric", "cinnamon", "cardamom",
    "cloves", "star anise", "fennel seeds", "mustard seeds", "mustard",
    "dijon mustard", "wholegrain mustard", "bay leaf", "bay leaves",
    "parsley", "basil", "thyme", "rosemary", "oregano", "sage", "mint",
    "dill", "tarragon", "chives", "saffron", "vanilla", "vanilla extract",
    "sugar", "caster sugar", "granulated sugar", "brown sugar",
    "icing sugar", "honey", "maple syrup", "golden syrup", "treacle",
    "molasses", "agave syrup", "baking soda", "bicarbonate of soda",
    "yeast", "dried yeast", "cocoa", "cocoa powder", "cacao",
    "water", "cold water", "boiling water", "ice", "vinegar",
    "white wine vinegar", "red wine vinegar", "cider vinegar",
    "apple cider vinegar", "tea", "green tea", "coffee", "espresso",
)
