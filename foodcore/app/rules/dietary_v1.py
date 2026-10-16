"""Dietary rule data, version 1 — DATA ONLY, no logic.

Two tables and one diet registry:

* :data:`CLASS_TERMS` — ingredient term → the *classes* it belongs to
  (``meat``, ``fish``, ``animal_rennet``, ``grain``, …). Membership is a fact
  about the ingredient, not about any diet.
* :data:`TERM_NOTES` — why a term is in a soft class, in words a user can read.
  Required for every term in a soft class; the screener asserts it.
* :data:`DIETS` — each diet names which classes are **incompatible**, which are
  merely **uncertain**, and which allergen groups it delegates to the allergen
  engine. That delegation is deliberate: ``dairy_free`` must honour the
  material-boundary rule ("almond milk" is not dairy), and the allergen engine
  is the one place that rule lives.

Soft classes are where the spec's own example lives: Parmesan is traditionally
made with animal rennet, so ``parmesan`` is in ``animal_rennet``, which
``vegetarian`` lists as *uncertain*. It therefore never reports as
vegetarian-compatible on its own.

``advisory`` is honesty, not decoration. Whole30 and paleo have no machine-
checkable authority (added-sugar rules, approved-additive lists, program
phases), and keto/low-carb need nutrient data that foodcore does not hold yet.
Those diets screen for obvious conflicts and mark the result unverified rather
than claiming a clearance they cannot support.
"""
from __future__ import annotations

VERSION = "dietary/2026.10.1"
LICENSE = "CC0-1.0"
# When this rule data was last reviewed against its sources. Recorded on every
# piece of evidence as the provenance `retrieved` field.
RETRIEVED = "2026-10-16"
SOURCE = "foodcore.rules.dietary_v1"

CLASS_TERMS: dict[str, tuple[str, ...]] = {
    # --- hard classes --------------------------------------------------------
    "meat": (
        "meat", "chicken", "chicken breast", "chicken breasts", "chicken thigh",
        "chicken thighs", "chicken wings", "whole chicken", "beef", "beef mince",
        "minced beef", "ground beef", "steak", "braising steak", "brisket",
        "pork", "pork belly", "pork chop", "pork chops", "pork mince", "bacon",
        "pancetta", "guanciale", "ham", "gammon", "prosciutto", "salami",
        "chorizo", "pepperoni", "lamb", "lamb mince", "leg of lamb", "mutton",
        "turkey", "turkey mince", "duck", "duck breast", "duck fat", "goose",
        "venison", "rabbit", "veal", "liver", "kidney", "oxtail", "ox cheek",
        "mince", "sausage", "sausages", "black pudding", "haggis", "pate",
        "chicken stock", "beef stock", "bone broth", "chicken broth",
        "beef dripping", "schmaltz",
    ),
    "fish": (
        "fish", "anchovy", "anchovies", "sardine", "sardines", "salmon", "tuna",
        "cod", "haddock", "mackerel", "trout", "sea bass", "pollock", "tilapia",
        "halibut", "sole", "snapper", "hake", "herring", "kipper", "whitebait",
        "monkfish", "plaice", "eel", "fish sauce", "nam pla", "fish stock",
        "worcestershire sauce", "caviar", "roe", "bonito", "katsuobushi",
        "dashi", "surimi", "crab stick", "crab sticks",
    ),
    "shellfish": (
        "shellfish", "crustacean", "crustaceans", "shrimp", "shrimps", "prawn",
        "prawns", "king prawn", "king prawns", "crab", "crab meat", "lobster",
        "langoustine", "crayfish", "crawfish", "krill", "scampi",
        "shrimp paste", "belacan", "mollusc", "molluscs", "mussel", "mussels",
        "clam", "clams", "oyster", "oysters", "oyster sauce", "squid",
        "calamari", "octopus", "scallop", "scallops", "cockle", "cockles",
        "whelk", "abalone", "snail", "escargot",
    ),
    # Derived from slaughter — vegetarians avoid these as well as vegans.
    "animal_slaughter_derived": (
        "gelatin", "gelatine", "lard", "suet", "tallow", "dripping",
        "isinglass", "rennet", "animal rennet", "pepsin", "marshmallow",
        "marshmallows", "collagen", "bone char", "cochineal", "carmine",
        "l cysteine",
    ),
    # Animal by-products — vegans avoid, vegetarians generally do not.
    "animal_byproduct": (
        "honey", "royal jelly", "beeswax", "propolis", "shellac",
        "confectioners glaze", "lanolin",
    ),
    "grain": (
        "wheat", "wheat flour", "flour", "plain flour", "all purpose flour",
        "bread flour", "self raising flour", "wholemeal flour", "bread",
        "breadcrumbs", "panko", "croutons", "pasta", "spaghetti", "macaroni",
        "penne", "noodles", "rice", "white rice", "brown rice", "basmati rice",
        "jasmine rice", "arborio rice", "risotto rice", "rice flour", "oats",
        "rolled oats", "porridge oats", "oat flour", "barley", "rye", "spelt",
        "semolina", "couscous", "bulgur", "farro", "freekeh", "quinoa",
        "millet", "sorghum", "teff", "amaranth", "buckwheat", "corn",
        "sweetcorn", "sweet corn", "corn flour", "cornflour", "cornstarch",
        "corn starch", "polenta", "tortilla", "tortillas", "corn tortilla",
        "cereal", "cracker", "crackers", "biscuit", "biscuits", "pastry",
        "puff pastry", "filo", "phyllo", "pita", "pitta", "naan", "seitan",
        "beer", "ale", "lager", "stout", "malt",
    ),
    "legume": (
        "legume", "legumes", "peanut", "peanuts", "peanut butter",
        "groundnut", "soy", "soya", "soybean", "soybeans", "soy sauce",
        "tamari", "tofu", "tempeh", "miso", "edamame", "soy milk", "soya milk",
        "lentil", "lentils", "red lentils", "green lentils", "puy lentils",
        "chickpea", "chickpeas", "gram flour", "chickpea flour", "hummus",
        "houmous", "black beans", "kidney beans", "cannellini beans",
        "butter beans", "borlotti beans", "haricot beans", "broad beans",
        "green beans", "runner beans", "split peas", "peas",
        "sugar snap peas", "mangetout", "pea protein",
    ),
    "refined_sugar": (
        "sugar", "caster sugar", "granulated sugar", "brown sugar",
        "icing sugar", "golden syrup", "corn syrup", "glucose syrup",
        "high fructose corn syrup", "treacle", "molasses", "agave syrup",
        "maple syrup", "honey", "sucrose", "dextrose", "fructose",
    ),
    "high_carb": (
        "sugar", "caster sugar", "granulated sugar", "brown sugar",
        "icing sugar", "golden syrup", "corn syrup", "maple syrup", "honey",
        "flour", "plain flour", "all purpose flour", "bread", "breadcrumbs",
        "pasta", "spaghetti", "macaroni", "noodles", "rice", "white rice",
        "brown rice", "basmati rice", "risotto rice", "potato", "potatoes",
        "sweet potato", "sweet potatoes", "parsnip", "parsnips", "oats",
        "rolled oats", "cereal", "couscous", "quinoa", "polenta", "banana",
        "bananas", "date", "dates", "raisin", "raisins", "sultanas",
        "mango", "pineapple", "beer", "ale", "lager", "cider",
    ),
    "alcohol": (
        "wine", "white wine", "red wine", "rose wine", "beer", "ale", "lager",
        "stout", "cider", "vodka", "gin", "rum", "whisky", "whiskey",
        "brandy", "sherry", "port", "marsala", "mirin", "vermouth",
        "amaretto", "kirsch", "creme de menthe", "bitters",
    ),
    # --- soft classes: real uncertainty, never a silent clearance -----------
    "animal_rennet": (
        "parmesan", "parmigiano reggiano", "grana padano", "pecorino",
        "pecorino romano", "gruyere", "manchego", "emmental", "comte",
        "cheese", "hard cheese", "mozzarella", "gorgonzola", "roquefort",
        "cheddar", "feta", "halloumi", "provolone", "gouda", "edam",
        "gruyere", "stilton", "brie", "camembert",
    ),
    "animal_fining": (
        "wine", "white wine", "red wine", "rose wine", "beer", "ale", "lager",
        "stout", "cider",
    ),
    "animal_fat_risk": (
        "pastry", "puff pastry", "shortcrust pastry", "filo", "phyllo",
        "refried beans", "tortilla", "tortillas", "naan", "brioche",
        "fried rice",
    ),
    "animal_origin_unspecified": (
        "stock", "broth", "stock cube", "stock cubes", "bouillon", "gravy",
        "gravy granules", "natural flavouring", "natural flavoring",
        "mono and diglycerides", "monoglycerides", "diglycerides",
        "vitamin d3", "omega 3", "caesar dressing", "caesar salad dressing",
        "kimchi", "glycerin", "glycerine", "lecithin", "enzymes",
        "flavouring", "flavoring", "shortening", "margarine",
        "worcestershire", "sauce", "dressing", "marinade",
    ),
}

# Why a soft-class term is uncertain, in user-facing words. Every term in a
# soft class needs one; the screener asserts this at load so a rule cannot be
# added without its explanation.
TERM_NOTES: dict[str, str] = {
    "parmesan": "traditionally made with animal rennet",
    "parmigiano reggiano": "PDO rules require calf rennet",
    "grana padano": "traditionally made with animal rennet",
    "pecorino": "traditionally made with animal rennet",
    "pecorino romano": "traditionally made with animal rennet",
    "gruyere": "traditionally made with animal rennet",
    "manchego": "traditionally made with animal rennet",
    "emmental": "traditionally made with animal rennet",
    "comte": "traditionally made with animal rennet",
    "cheese": "rennet source unspecified; may be animal or microbial",
    "hard cheese": "rennet source unspecified; may be animal or microbial",
    "mozzarella": "rennet source unspecified; may be animal or microbial",
    "gorgonzola": "traditionally made with animal rennet",
    "roquefort": "traditionally made with animal rennet",
    "cheddar": "most cheddar is made with animal rennet",
    "feta": "traditionally made with animal rennet",
    "halloumi": "rennet source varies by producer",
    "provolone": "traditionally made with animal rennet",
    "gouda": "rennet source varies by producer",
    "edam": "rennet source varies by producer",
    "stilton": "traditionally made with animal rennet",
    "brie": "rennet source varies by producer",
    "camembert": "traditionally made with animal rennet",
    "wine": "often fined with isinglass, gelatin or egg albumen",
    "white wine": "often fined with isinglass, gelatin or egg albumen",
    "red wine": "often fined with isinglass, gelatin or egg albumen",
    "rose wine": "often fined with isinglass, gelatin or egg albumen",
    "beer": "may be fined with isinglass",
    "ale": "may be fined with isinglass",
    "lager": "may be fined with isinglass",
    "stout": "may be fined with isinglass",
    "cider": "may be fined with gelatin",
    "pastry": "may be made with lard or butter",
    "puff pastry": "may be made with lard or butter",
    "shortcrust pastry": "may be made with lard or butter",
    "filo": "may be brushed with butter",
    "phyllo": "may be brushed with butter",
    "refried beans": "traditionally made with lard",
    "tortilla": "flour tortillas may contain lard",
    "tortillas": "flour tortillas may contain lard",
    "naan": "usually enriched with yoghurt or ghee",
    "brioche": "enriched with butter and egg",
    "fried rice": "commonly cooked with egg or animal fat",
    "stock": "unspecified stock is commonly meat-based",
    "broth": "unspecified broth is commonly meat-based",
    "stock cube": "unspecified stock is commonly meat-based",
    "stock cubes": "unspecified stock is commonly meat-based",
    "bouillon": "unspecified bouillon is commonly meat-based",
    "gravy": "gravy is commonly meat-based",
    "gravy granules": "gravy is commonly meat-based",
    "natural flavouring": "undeclared flavourings may be animal-derived",
    "natural flavoring": "undeclared flavourings may be animal-derived",
    "flavouring": "undeclared flavourings may be animal-derived",
    "flavoring": "undeclared flavourings may be animal-derived",
    "mono and diglycerides": "may be animal- or plant-derived",
    "monoglycerides": "may be animal- or plant-derived",
    "diglycerides": "may be animal- or plant-derived",
    "vitamin d3": "usually derived from lanolin or fish oil",
    "omega 3": "commonly fish-derived",
    "caesar dressing": "classic recipes contain anchovy and egg",
    "caesar salad dressing": "classic recipes contain anchovy and egg",
    "kimchi": "many recipes include fish sauce or salted shrimp",
    "glycerin": "may be animal- or plant-derived",
    "glycerine": "may be animal- or plant-derived",
    "lecithin": "usually soy, occasionally egg",
    "enzymes": "source unspecified; may be animal-derived",
    "shortening": "may contain animal fat",
    "margarine": "may contain whey, gelatin or fish-derived vitamins",
    "worcestershire": "classic recipes contain anchovy",
    "sauce": "composition undeclared",
    "dressing": "composition undeclared",
    "marinade": "composition undeclared",
}

SOFT_CLASSES: tuple[str, ...] = (
    "animal_rennet", "animal_fining", "animal_fat_risk",
    "animal_origin_unspecified",
)

# diet -> rule. `incompatible` and `uncertain` are class names from CLASS_TERMS;
# `allergen_groups` are delegated to the allergen engine (one material-boundary
# rule, not two).
DIETS: dict[str, dict[str, object]] = {
    "vegan": {
        "label": "Vegan",
        "incompatible": ("meat", "fish", "shellfish",
                         "animal_slaughter_derived", "animal_byproduct"),
        "uncertain": SOFT_CLASSES,
        "allergen_groups": ("milk", "egg"),
        "advisory": None,
    },
    "vegetarian": {
        "label": "Vegetarian",
        "incompatible": ("meat", "fish", "shellfish", "animal_slaughter_derived"),
        "uncertain": SOFT_CLASSES,
        "allergen_groups": (),
        "advisory": None,
    },
    "pescatarian": {
        "label": "Pescatarian",
        "incompatible": ("meat", "animal_slaughter_derived"),
        "uncertain": ("animal_rennet", "animal_fat_risk",
                      "animal_origin_unspecified"),
        "allergen_groups": (),
        "advisory": None,
    },
    "gluten_free": {
        "label": "Gluten-free",
        "incompatible": (),
        "uncertain": (),
        "allergen_groups": ("gluten", "wheat"),
        "advisory": (
            "Screens ingredients only. Gluten-free certification also depends "
            "on cross-contact in the kitchen, which is not screenable here."
        ),
    },
    "dairy_free": {
        "label": "Dairy-free",
        "incompatible": (),
        "uncertain": (),
        "allergen_groups": ("milk",),
        "advisory": None,
    },
    "keto": {
        "label": "Keto",
        "incompatible": (),
        "uncertain": ("high_carb",),
        "allergen_groups": (),
        "advisory": (
            "Informational: keto is a daily carbohydrate budget, and foodcore "
            "holds no nutrient data yet. High-carbohydrate ingredients are "
            "flagged, not counted."
        ),
    },
    "low_carb": {
        "label": "Low-carb",
        "incompatible": (),
        "uncertain": ("high_carb",),
        "allergen_groups": (),
        "advisory": (
            "Informational: no carbohydrate arithmetic is performed. "
            "High-carbohydrate ingredients are flagged, not counted."
        ),
    },
    "paleo": {
        "label": "Paleo",
        "incompatible": ("grain", "legume", "refined_sugar"),
        "uncertain": ("alcohol",) + SOFT_CLASSES,
        "allergen_groups": ("milk",),
        "advisory": (
            "Informational: paleo has no single authority, so this is a screen "
            "over commonly excluded groups, not a program certification."
        ),
    },
    "whole30": {
        "label": "Whole30",
        "incompatible": ("grain", "legume", "refined_sugar", "alcohol"),
        "uncertain": SOFT_CLASSES,
        "allergen_groups": ("milk",),
        "advisory": (
            "Informational: Whole30 program rules (added sugar in any form, "
            "approved additives, recreated treats) are not verified here."
        ),
    },
}
