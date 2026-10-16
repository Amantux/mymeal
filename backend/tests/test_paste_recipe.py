"""Pasting a recipe that ISN'T structured data: a plain-text blob, a page copied
out of a browser (styled HTML, CSS and all), or microdata markup.

All three used to dead-end at "no AI provider available for recipe parsing" —
the content was sitting right there, correctly labelled in two of the three
cases, and the app asked a language model to re-derive it (or refused outright
for anyone with no provider set up).
"""
import pytest

import app.api.ai as ai_api
from app.services.ai.recipe_import import import_recipe
from app.services.recipe_parse import (
    extract_microdata_recipe,
    html_to_lines,
    parse_pasted,
    parse_recipe_text,
    segment_runon_lines,
)

BLOB = """Grandma's Lemon Drizzle Cake
Serves 8 | Prep 20 min | Bake 45 min

Ingredients
225g unsalted butter, softened
225g caster sugar
4 large eggs

For the drizzle:
85g caster sugar
Juice of 2 lemons

Method
1. Heat the oven to 180C.
2. Beat the butter and sugar until pale.
3. Bake for 45 minutes.
"""

# What the clipboard actually holds after selecting a rendered recipe and
# copying: a styled fragment. Note there is NO <script> — every browser strips
# those on copy, which is exactly why JSON-LD extraction cannot help here.
COPIED_HTML = """<div class="recipe-card" style="font-family:Georgia">
<style>.recipe-card h2 { margin: 0; color: #333 }</style>
<h2>Lemon Drizzle Cake</h2>
<p class="recipe-yield">Serves 8</p>
<ul class="recipe-ingredients">
  <li>225g unsalted butter</li><li>4 large eggs</li><li>225g self-raising flour</li>
</ul>
<ol class="recipe-instructions">
  <li>Heat the oven to 180C.</li><li>Beat the butter and sugar.</li>
</ol></div>"""

MICRODATA = """<div itemscope itemtype="https://schema.org/Recipe">
  <h1 itemprop="name">Microdata Muffins</h1>
  <meta itemprop="prepTime" content="PT15M">
  <meta itemprop="cookTime" content="PT25M">
  <span itemprop="recipeYield">12 muffins</span>
  <li itemprop="recipeIngredient">2 cups flour</li>
  <li itemprop="recipeIngredient">1 cup milk</li>
  <li itemprop="recipeIngredient">2 eggs</li>
  <div itemprop="recipeInstructions">Mix the dry ingredients.</div>
  <div itemprop="recipeInstructions">Bake at 200C for 25 minutes.</div>
</div>"""


# --- The three paste shapes, none of which may need a provider ---------------

def test_a_plain_text_blob_imports_with_no_provider():
    got = import_recipe(text=BLOB, provider=None)

    assert got["name"] == "Grandma's Lemon Drizzle Cake"
    assert [i["display"] for i in got["ingredients"]][:2] == [
        "225g unsalted butter, softened", "225g caster sugar"]
    assert [s["text"] for s in got["steps"]] == [
        "Heat the oven to 180C.",
        "Beat the butter and sugar until pale.",
        "Bake for 45 minutes.",
    ]


def test_html_copied_from_a_browser_imports_with_no_provider():
    got = import_recipe(text=COPIED_HTML, provider=None)

    assert got["name"] == "Lemon Drizzle Cake"
    assert [i["display"] for i in got["ingredients"]] == [
        "225g unsalted butter", "4 large eggs", "225g self-raising flour"]
    assert [s["text"] for s in got["steps"]] == [
        "Heat the oven to 180C.", "Beat the butter and sugar."]


def test_microdata_imports_with_no_provider():
    got = import_recipe(text=MICRODATA, provider=None)

    assert got["name"] == "Microdata Muffins"
    assert len(got["ingredients"]) == 3
    assert got["prepMinutes"] == 15 and got["cookMinutes"] == 25
    assert got["servings"] == 12


def test_the_model_is_never_asked_when_the_paste_can_be_read():
    """Same contract as the JSON-LD path: deterministic first, model last. A
    provider being present must not change the result or cost a call."""
    class Boom:
        def complete_json(self, *a, **k):
            raise AssertionError("the model was called for a readable paste")

    for text in (BLOB, COPIED_HTML, MICRODATA):
        assert import_recipe(text=text, provider=Boom())["ingredients"]


# --- Structure the parser has to get right ----------------------------------

def test_sub_headings_become_ingredient_sections():
    """"For the drizzle:" is a label, not an ingredient. RecipeIngredient.section
    already exists and round-trips, so the grouping is kept rather than flattened."""
    rows = parse_recipe_text(BLOB)["ingredients"]

    assert [r.get("section", "") for r in rows] == [
        "", "", "", "For the drizzle", "For the drizzle"]
    assert "For the drizzle" not in [r["display"] for r in rows]


def test_a_main_heading_is_not_mistaken_for_a_section():
    """"Ingredients:" also ends in a colon; treating it as a sub-heading would
    name every following row after it."""
    rows = parse_recipe_text(
        "Cake\n\nIngredients:\n2 cups flour\n1 cup milk\n\nMethod:\n1. Mix.\n"
    )["ingredients"]

    assert [r["display"] for r in rows] == ["2 cups flour", "1 cup milk"]
    assert not any(r.get("section") for r in rows)


def test_step_numbering_is_stripped_in_every_common_form():
    got = parse_recipe_text(
        "Thing\nIngredients\n1 cup flour\n2 eggs\n"
        "Method\n1. First.\n2) Second.\nStep 3: Third.\n4 - Fourth.\n")

    assert [s["text"] for s in got["steps"]] == [
        "First.", "Second.", "Third.", "Fourth."]


def test_a_hard_wrapped_step_is_rejoined_not_split():
    """Copying from a narrow column wraps mid-sentence. A fragment that starts
    lowercase and isn't numbered continues the step above it."""
    got = parse_recipe_text(
        "Thing\nIngredients\n1 cup flour\n2 eggs\n"
        "Method\n1. Warm the milk gently until it is\nsteaming but not boiling.\n"
        "2. Whisk in the flour.\n")

    assert [s["text"] for s in got["steps"]] == [
        "Warm the milk gently until it is steaming but not boiling.",
        "Whisk in the flour.",
    ]


def test_a_blob_with_no_headings_is_split_by_classifying_lines():
    got = parse_recipe_text(
        "Simple Tomato Pasta\n400g spaghetti\n2 tbsp olive oil\n3 cloves garlic\n"
        "Salt and pepper\n"
        "Bring a large pan of salted water to the boil and cook the spaghetti.\n"
        "Add the tomatoes and simmer for 10 minutes.\n")

    assert got["name"] == "Simple Tomato Pasta"
    assert [i["display"] for i in got["ingredients"]] == [
        "400g spaghetti", "2 tbsp olive oil", "3 cloves garlic", "Salt and pepper"]
    assert len(got["steps"]) == 2


@pytest.mark.parametrize("line,text", [
    ("Serves 8", 8), ("Serves: 8-10", 8), ("Makes 24 cookies", 24),
    ("Yield: 12 muffins", 12), ("4 servings", 4),
])
def test_servings_are_read_from_the_header_lines(line, text):
    got = parse_recipe_text(f"Thing\n{line}\nIngredients\n1 cup flour\n2 eggs\n")

    assert got["servings"] == text


@pytest.mark.parametrize("line,prep,cook", [
    ("Prep 20 min | Cook 45 min", 20, 45),
    ("Prep time: 1 hr 15 mins", 75, 0),
    ("Hands-on 10 minutes, baking 1 hour", 10, 60),
])
def test_times_are_read_from_the_header_lines(line, prep, cook):
    got = parse_recipe_text(f"Thing\n{line}\nIngredients\n1 cup flour\n2 eggs\n")

    assert (got["prepMinutes"], got["cookMinutes"]) == (prep, cook)
    assert got["totalMinutes"] == prep + cook       # derived when not stated


# --- Refusing, rather than inventing ----------------------------------------

@pytest.mark.parametrize("text", [
    "",
    "just one line",
    "A title\nand a sentence about nothing in particular.",
    "Hello\nHow are you\nSee you soon",          # prose, no amounts
])
def test_text_that_isnt_a_recipe_falls_through_instead_of_guessing(text):
    """None means "I could not read this" and the caller goes to the model. A
    one-ingredient invention would be worse than admitting it."""
    assert parse_pasted(text) is None


def test_a_readable_paste_is_preferred_but_an_unreadable_one_still_needs_ai():
    with pytest.raises(ValueError, match="no AI provider"):
        import_recipe(text="not a recipe at all, just chatter", provider=None)


# --- HTML handling ----------------------------------------------------------

def test_css_and_scripts_are_ignored_not_read_as_ingredients():
    lines = html_to_lines(
        '<style>.a{color:red;margin:0}</style><script>var x=1</script>'
        '<ul><li>2 cups flour</li></ul>')

    assert "color:red" not in lines and "var x" not in lines
    assert "2 cups flour" in lines


def test_list_structure_survives_the_flattening():
    """One line per item is the difference between an ingredient list and one
    run-on sentence — the whole text parser depends on it."""
    lines = html_to_lines("<ul><li>2 cups flour</li><li>1 cup milk</li></ul>")

    # No class to go on here, so no synthetic heading is invented — just the
    # items, one per line, which is what the text parser needs.
    assert lines.splitlines() == ["2 cups flour", "1 cup milk"]


def test_class_names_label_the_sections():
    """`class="recipe-ingredients"` survives a copy and is a far stronger signal
    than guessing from prose."""
    lines = html_to_lines(COPIED_HTML).splitlines()

    assert "Ingredients" in lines and "Instructions" in lines


def test_microdata_without_an_itemscope_wrapper_still_reads():
    """A copied fragment often loses the element that carried the itemtype."""
    node = extract_microdata_recipe(
        '<li itemprop="recipeIngredient">2 cups flour</li>'
        '<li itemprop="recipeIngredient">1 cup milk</li>')

    assert node is not None
    assert node["recipeIngredient"] == ["2 cups flour", "1 cup milk"]


def test_microdata_needs_enough_to_be_a_recipe():
    assert extract_microdata_recipe('<li itemprop="recipeIngredient">salt</li>') is None
    assert extract_microdata_recipe("<div>nothing here</div>") is None


def test_angle_brackets_in_a_plain_recipe_are_not_treated_as_markup():
    """"<1 cup" is text, not HTML — routing it through the HTML path would
    silently drop the line."""
    got = parse_pasted(
        "Thing\nIngredients\n<1 cup flour\n2 eggs\nMethod\n1. Mix well.\n")

    assert [i["display"] for i in got["ingredients"]] == ["<1 cup flour", "2 eggs"]


# --- Contract: one payload shape --------------------------------------------

def test_payload_shapes_match():
    """The deterministic parser and normalize_jsonld build the same payload. Two
    builders that drift is how a field ends up silently missing on one path."""
    from app.services.ai.recipe_import import normalize_jsonld

    theirs = normalize_jsonld({"@type": "Recipe", "name": "x"})
    mine = parse_recipe_text(BLOB)

    assert set(mine) == set(theirs)
    # Types too, not only key names: a matching key holding a str where the other
    # holds an int is exactly the drift this guard exists to catch, and comparing
    # names alone passed straight through it. None is skipped rather than compared
    # — cookTemperatureC is legitimately nullable, and only one of these two
    # inputs mentions an oven temperature.
    for key in theirs:
        a, b = mine[key], theirs[key]
        if a is None or b is None:
            continue
        assert type(a) is type(b), f"{key}: {type(a).__name__} vs {type(b).__name__}"


# --- Through the endpoint ---------------------------------------------------

def test_pasting_a_blob_through_the_endpoint_saves_the_recipe(auth_client, monkeypatch):
    def no_provider():
        from app.services.ai.base import ProviderError
        raise ProviderError("none configured")
    monkeypatch.setattr(ai_api, "get_provider", no_provider)

    r = auth_client.post("/api/v1/ai/import", json={"text": BLOB})

    assert r.status_code == 201
    body = r.get_json()
    assert body["name"] == "Grandma's Lemon Drizzle Cake"
    assert len(body["ingredients"]) == 5
    assert len(body["steps"]) == 3


def test_pasting_copied_html_through_the_endpoint_keeps_the_sections(auth_client,
                                                                     monkeypatch):
    def no_provider():
        from app.services.ai.base import ProviderError
        raise ProviderError("none configured")
    monkeypatch.setattr(ai_api, "get_provider", no_provider)

    rid = auth_client.post("/api/v1/ai/import", json={"text": BLOB}).get_json()["id"]
    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"]

    # The section survives the round-trip to the database, not just the parse.
    assert [i["section"] for i in got][-2:] == ["For the drizzle", "For the drizzle"]


# --- Refusing pages that are not recipes -------------------------------------
#
# Every one of these was PROVEN to import as a recipe before the acceptance gate
# was tightened: the run-end rule ("stop after two consecutive misses") let the
# ingredient list swallow whole documents, because prose alternates short and
# long lines and the counter never reached two.

KNIFE_REVIEW = """10 Best Kitchen Knives of 2026
Home
Reviews
By Jane Doe
Price: $150
We tested 30 knives over six months in a busy test kitchen.
The Global G-2 remains our top pick for most cooks.
Great list!
I love my Global.
Price: $89
"""


def test_an_article_with_a_few_numbers_in_it_is_not_a_recipe():
    """It imported as a 16-ingredient recipe — "Home", "By Jane Doe",
    "Price: $150", "Great list!" — with a 201 and no steps."""
    assert parse_pasted(KNIFE_REVIEW) is None


def test_the_unheaded_path_demands_a_method():
    """A list of amounts with no instructions is a shopping list, not a recipe,
    and accepting it is how prose with two prices in it became an import."""
    assert parse_recipe_text("Stuff\n2 cups flour\n1 cup milk\n3 eggs\n") is None


def test_the_unheaded_path_demands_mostly_measurements():
    got = parse_recipe_text(
        "Thing\n2 cups flour\nsome nice words\nmore nice words\n"
        "another line entirely\nWhisk it all together and cook.\n")

    assert got is None


# --- Section markers must not become recipe content --------------------------

WPRM = """<div class="wprm-recipe-ingredients-container"><h3>Ingredients</h3>
<ul class="wprm-recipe-ingredients"><li>225g butter</li><li>4 eggs</li><li>200g flour</li></ul></div>
<div class="wprm-recipe-instructions-container"><h3>Instructions</h3>
<ol class="wprm-recipe-instructions"><li>Heat oven.</li><li>Bake.</li></ol></div>"""


def test_nested_containers_do_not_emit_a_heading_twice():
    """The WP-Recipe-Maker DOM nests *-ingredients-container around
    ul.*-ingredients AND prints its own <h3>Ingredients</h3>. Marking both, next
    to a heading the page already had, put "Ingredients" in the list twice — as
    ingredients."""
    got = parse_pasted(WPRM)

    assert [i["display"] for i in got["ingredients"]] == [
        "225g butter", "4 eggs", "200g flour"]
    assert [s["text"] for s in got["steps"]] == ["Heat oven.", "Bake."]


def test_a_per_item_element_is_not_labelled():
    """One <div itemprop="recipeInstructions"> PER STEP emitted one heading per
    step."""
    got = parse_pasted(
        '<ul class="ingredients"><li>2 cups flour</li><li>1 cup milk</li></ul>'
        '<div itemprop="recipeInstructions">Mix.</div>'
        '<div itemprop="recipeInstructions">Bake.</div>')

    assert [s["text"] for s in got["steps"]] == ["Mix.", "Bake."]


def test_a_class_that_merely_contains_an_ingredient_word_is_not_a_list():
    lines = html_to_lines('<div class="no-ingredients-needed">Nothing to buy.</div>')

    assert "Ingredients" not in lines.splitlines()


# --- Where the recipe ends ---------------------------------------------------

def test_a_trailing_notes_section_does_not_become_method_steps():
    got = parse_recipe_text(
        "Banana Bread\nIngredients\n3 ripe bananas\n200g flour\n"
        "Method\n1. Mash the bananas.\n2. Bake for 50 minutes.\n"
        "Notes\nBest eaten within two days.\nTips\nUse very ripe bananas.\n")

    assert [s["text"] for s in got["steps"]] == [
        "Mash the bananas.", "Bake for 50 minutes."]


def test_comments_copied_along_with_a_recipe_are_dropped():
    got = parse_recipe_text(
        "Chili\nIngredients\n1 tbsp olive oil\n2 tins tomatoes\n"
        "Method\n1. Fry the onion.\n2. Simmer.\n"
        "Comments\nMade this twice!\nAdded chipotle, delicious.\n")

    assert [s["text"] for s in got["steps"]] == ["Fry the onion.", "Simmer."]


# --- Headings in other languages ---------------------------------------------

def test_a_german_recipe_does_not_import_its_headings_as_ingredients():
    """Before, "Zutaten" and "Zubereitung" became ingredient rows — worse than
    the old behaviour, where the model handled it correctly."""
    got = parse_recipe_text(
        "Pfannkuchen\nZutaten\n250 g Mehl\n500 ml Milch\n3 Eier\n"
        "Zubereitung\n1. Alles verruehren.\n2. Braten.\n")

    assert [i["display"] for i in got["ingredients"]] == [
        "250 g Mehl", "500 ml Milch", "3 Eier"]
    assert [s["text"] for s in got["steps"]] == ["Alles verruehren.", "Braten."]


# --- Titles ------------------------------------------------------------------

def test_a_short_title_is_not_eaten_as_an_ingredient():
    got = parse_recipe_text(
        "Pancakes\n2 cups flour\n1 cup milk\n2 eggs\nWhisk and fry until golden.\n")

    assert got["name"] == "Pancakes"
    assert [i["display"] for i in got["ingredients"]] == [
        "2 cups flour", "1 cup milk", "2 eggs"]


def test_a_blob_that_starts_with_an_ingredient_keeps_it():
    """Consuming line 1 as the title unconditionally invented a name AND deleted
    an ingredient."""
    got = parse_recipe_text(
        "2 cups flour\n1 cup milk\n2 eggs\nWhisk and fry until golden brown.\n")

    assert [i["display"] for i in got["ingredients"]] == [
        "2 cups flour", "1 cup milk", "2 eggs"]


# --- The AI prompt must not lose content -------------------------------------

def test_ingredients_inside_a_form_survive_for_the_model():
    """Sites wrap ingredient lists in a form for "add to shopping list"
    checkboxes. Decomposing <form> deleted them before the model ever saw them —
    a silent quality drop on a path that used to work."""
    from app.services.ai.recipe_import import _visible_text

    out = _visible_text('<h1>Soup</h1><form class="ing-form"><ul><li>2 carrots</li>'
                        '<li>1 onion</li></ul></form><h2>Method</h2><p>Simmer.</p>')

    assert "2 carrots" in out and "1 onion" in out


def test_the_model_is_not_fed_synthetic_headings():
    """Markers exist for the deterministic parser; showing the model headings the
    page never printed puts words in the source's mouth."""
    from app.services.ai.recipe_import import _visible_text

    out = _visible_text('<ul class="recipe-ingredients"><li>2 cups flour</li></ul>')

    assert "Ingredients" not in out.splitlines()


def test_a_url_import_does_not_guess_structure_out_of_a_whole_page(monkeypatch):
    """Heuristics are paste-only. A paste is a deliberate selection; a fetched
    page is nav + comments + ads, and guessing there produced "Home" as the
    recipe title on a path that previously handed the page to the model and got
    a sane answer. With no provider it must now decline, not invent."""
    import app.services.ai.recipe_import as ri

    page = ("<html><body><nav><a>Home</a><a>Reviews</a></nav>"
            "<h1>Best Knives</h1><p>Price: $150</p>"
            "<p>We tested 30 knives over six months in a busy kitchen.</p>"
            "<p>The Global G-2 is our top pick for most cooks.</p>"
            "<p>Price: $89</p></body></html>")
    monkeypatch.setattr(ri, "_fetch", lambda url: page)

    with pytest.raises(ValueError, match="no AI provider"):
        ri.import_recipe(url="https://example.com/knives", provider=None)


def test_a_url_import_still_reads_microdata_without_a_provider(monkeypatch):
    """Only the GUESSING is paste-only — exact markup on a URL still skips the
    model, which is the behaviour this whole path exists for."""
    import app.services.ai.recipe_import as ri
    monkeypatch.setattr(ri, "_fetch", lambda url: MICRODATA)

    got = ri.import_recipe(url="https://example.com/muffins", provider=None)

    assert got["name"] == "Microdata Muffins"
    assert got["sourceUrl"] == "https://example.com/muffins"


def test_a_huge_paste_is_capped():
    got = parse_recipe_text("Thing\nIngredients\n" + "1 cup flour\n" * 50000)

    assert got is None or len(got["ingredients"]) < 2000


# --- Run-on blobs: recovering line boundaries the source threw away ----------
#
# Copied verbatim from the paste that prompted this: the site wraps each amount
# and each name in adjacent inline elements, so the clipboard holds ONE line
# with no separator anywhere. Note "¹/₂" is a superscript-solidus-subscript
# trio, not "½" — that is what the page renders.
RUN_ON = (
    "240 gramsEgg Whites(240 g)"
    "40 gramsParmesan Cheese finely grated(40 g)"
    "1 gramGarlic Powder or ¹/₂ teaspoon(1 g)"
    "1 gramOnion Powder or ¹/₂ teaspoon(1 g)"
    "¹/₂ Gram Black Pepperorns or ¹/₄ teaspoon"
    "1 gramItalian Seasoning optional(1 g)"
    "1 gramPaprika or red pepper flakes optional(1 g)"
    "Oil or 1 tsps oil for the pan"
)


def test_a_run_on_ingredient_blob_is_split_at_every_glued_quantity():
    """The whole point: eight ingredients out of one unseparated line."""
    got, changed = segment_runon_lines(RUN_ON)

    assert changed is True
    assert got.splitlines() == [
        "240 grams Egg Whites(240 g)",
        "40 grams Parmesan Cheese finely grated(40 g)",
        "1 gram Garlic Powder or ¹/₂ teaspoon(1 g)",
        "1 gram Onion Powder or ¹/₂ teaspoon(1 g)",
        "¹/₂ Gram Black Pepperorns or ¹/₄ teaspoon",
        "1 gram Italian Seasoning optional(1 g)",
        "1 gram Paprika or red pepper flakes optional(1 g)",
        "Oil or 1 tsps oil for the pan",
    ]


def test_an_alternative_measure_inside_a_line_is_not_a_boundary():
    """"or ¹/₂ teaspoon" belongs to the line it qualifies. A SPACE in front of
    the quantity is what says "same line"; only a glued one starts a new one."""
    got, _ = segment_runon_lines(RUN_ON)

    assert "or ¹/₂ teaspoon" in got.splitlines()[2]


# Every paste fixture in this suite, as the text parser actually receives it.
# Segmentation must be a no-op on all of them, byte for byte.
_TEXT_FIXTURES = [BLOB, KNIFE_REVIEW]
_HTML_FIXTURES = [COPIED_HTML, MICRODATA, WPRM]


@pytest.mark.parametrize("text", _TEXT_FIXTURES
                         + [html_to_lines(h) for h in _HTML_FIXTURES])
def test_existing_pastes_are_untouched_by_segmentation(text):
    got, changed = segment_runon_lines(text)

    assert changed is False
    assert got == text


@pytest.mark.parametrize("line", [
    "1 1/2 cups plain flour, sifted, plus 2 tablespoons for dusting the tin",
    "Bake in a 9x13 pan at 180°C for 45 minutes until the top is golden brown",
    "Use 1.5 kg of Grade A2 flour, a can of 7UP and 500ml of V8 for the batter",
    "Chop 2-3 cloves of garlic, then add 1 1/2 cups of water to the saucepan",
    "Beat the butter and sugar for 2 minutes, then fold in 200g of the flour",
])
def test_a_quantity_that_is_not_glued_to_a_word_is_never_a_boundary(line):
    """The traps. Several have a digit next to a letter; none has a bare
    quantity-plus-unit glued onto the end of the previous word."""
    got, changed = segment_runon_lines(line)

    assert changed is False
    assert got == line


def test_a_run_on_blob_imports_every_ingredient_instead_of_nothing():
    got = parse_recipe_text(RUN_ON)

    assert got is not None
    assert [i["display"] for i in got["ingredients"]] == [
        "240 grams Egg Whites(240 g)",
        "40 grams Parmesan Cheese finely grated(40 g)",
        "1 gram Garlic Powder or ¹/₂ teaspoon(1 g)",
        "1 gram Onion Powder or ¹/₂ teaspoon(1 g)",
        "¹/₂ Gram Black Pepperorns or ¹/₄ teaspoon",
        "1 gram Italian Seasoning optional(1 g)",
        "1 gram Paprika or red pepper flakes optional(1 g)",
        "Oil or 1 tsps oil for the pan",
    ]


def test_an_ingredient_list_pasted_on_its_own_is_not_thrown_away():
    """The unheaded path demands a method because its evidence is weak. A blob
    only segments on three or more glued amounts — a shape prose cannot make —
    so a bare ingredient list is accepted rather than discarded."""
    got = parse_recipe_text(RUN_ON)

    assert got["steps"] == []
    assert len(got["ingredients"]) == 8


def test_pasting_a_run_on_blob_through_the_endpoint_saves_every_ingredient(
        auth_client, monkeypatch):
    """End to end on the real paste route, with no provider configured — the
    situation the user was in when the whole paste was rejected."""
    def no_provider():
        from app.services.ai.base import ProviderError
        raise ProviderError("none configured")
    monkeypatch.setattr(ai_api, "get_provider", no_provider)

    r = auth_client.post("/api/v1/ai/import", json={"text": RUN_ON})

    assert r.status_code == 201
    assert len(r.get_json()["ingredients"]) == 8


# --- Refusing, which is the recoverable failure ------------------------------

@pytest.mark.parametrize("line", [
    # A segment that is only a measurement — the cuts landed between amounts,
    # so there is no ingredient there to name.
    "Sift the flour250 grams300 grams400 grams of golden caster sugar",
    # More rows than any recipe has.
    "Flour" + "100 gramsSugar" * 70,
    # One absurdly long segment: the method ran on into the list.
    ("2 cupsFlour1 cupSugar2 tspSalt" + "and then beat the mixture "
     "thoroughly until it is pale and completely smooth before baking " * 4),
])
def test_an_implausible_segmentation_is_refused_and_the_text_left_alone(line):
    """Refusing returns the original, so the existing "couldn't parse" path
    answers and the user still has the AI import. Silent garbage would not be
    recoverable; a refusal is."""
    got, changed = segment_runon_lines(line)

    assert changed is False
    assert got == line


# --- The same shape, applied to a numbered method ----------------------------

def test_a_run_on_numbered_method_is_split_into_steps():
    got = parse_recipe_text(
        "Ingredients\n2 cups plain flour\n1 tsp fine salt\nMethod\n"
        "1Mix the flour and the salt2Cover the bowl and rest for 30 minutes"
        "3Bake at 200C until golden")

    assert [s["text"] for s in got["steps"]] == [
        "Mix the flour and the salt",
        "Cover the bowl and rest for 30 minutes",
        "Bake at 200C until golden",
    ]


@pytest.mark.parametrize("line", [
    # The numbers must come out 1, 2, 3 … — in none of these do they, so this
    # is text that merely has digits next to capitals, not a numbered method.
    #
    # The first case is the one that isolates the run rule: it starts at 1 and
    # clears every other check, and only the gap between 1 and 3 refuses it.
    # (Mutation-checked — dropping the run rule splits this line in two.)
    "1Mix the flour and the salt3Bake it at 200C until it is golden brown",
    "mix the flour2Cover the bowl and rest5Bake it now in the very hot oven",
    "Take a Vitamin B12Supplement daily with a large glass of cold water",
    "Add 2Cups of the flour3Times over, then 9Bake it for a while to finish",
])
def test_digits_next_to_capitals_are_only_steps_when_they_run_in_order(line):
    got, changed = segment_runon_lines(line)

    assert changed is False
    assert got == line


# --- Inline elements are not line breaks -------------------------------------
#
# The HTML twin of the run-on blob above, and the commoner of the two: copying
# from a browser puts HTML on the clipboard, so this is the DEFAULT path for
# "paste a recipe from a site". The site wraps the amount, the name and the
# metric note in three adjacent <span>s; soup.get_text("\n") put a separator at
# every string boundary, so one <li> became three lines and every amount was
# orphaned from its food.

INLINE_SPANS = (
    '<div class="wprm-recipe-ingredients-container"><h3>Ingredients</h3>'
    '<ul class="wprm-recipe-ingredients">'
    '<li><span>240 grams</span><span>Egg Whites</span><span>(240 g)</span></li>'
    '<li><span>40 grams</span><span>Parmesan Cheese finely grated</span>'
    '<span>(40 g)</span></li>'
    '<li><span>1 gram</span><span>Garlic Powder or ¹⁄₂ teaspoon</span>'
    '<span>(1 g)</span></li>'
    '<li><span>1 gram</span><span>Onion Powder or ¹⁄₂ teaspoon</span>'
    '<span>(1 g)</span></li>'
    '<li><span>¹⁄₂ Gram</span>'
    '<span>Black Pepperorns or ¹⁄₄ teaspoon</span></li>'
    '<li><span>1 gram</span><span>Italian Seasoning optional</span>'
    '<span>(1 g)</span></li>'
    '<li><span>1 gram</span><span>Paprika or red pepper flakes optional</span>'
    '<span>(1 g)</span></li>'
    '<li><span>Oil or 1 tsps oil for the pan</span></li>'
    '</ul></div>'
    '<div class="wprm-recipe-instructions-container"><h3>Instructions</h3>'
    '<ol class="wprm-recipe-instructions">'
    '<li>Whisk the <b>egg whites</b> until frothy.</li>'
    '<li>Fold in the <span>Parmesan</span> and the seasonings.</li>'
    '<li>Cook in an oiled pan over medium heat.</li></ol></div>')

_INLINE_SPAN_ROWS = [
    "240 grams Egg Whites(240 g)",
    "40 grams Parmesan Cheese finely grated(40 g)",
    "1 gram Garlic Powder or ¹⁄₂ teaspoon(1 g)",
    "1 gram Onion Powder or ¹⁄₂ teaspoon(1 g)",
    "¹⁄₂ Gram Black Pepperorns or ¹⁄₄ teaspoon",
    "1 gram Italian Seasoning optional(1 g)",
    "1 gram Paprika or red pepper flakes optional(1 g)",
    "Oil or 1 tsps oil for the pan",
]


def test_spans_inside_one_list_item_stay_on_one_line():
    """Three <span>s in an <li> are ONE ingredient, not three lines. Note the
    expected rows are byte-identical to what the plain-text segmenter produces
    for the same recipe — the two paths must not disagree."""
    lines = html_to_lines(INLINE_SPANS).splitlines()

    assert lines == ["Ingredients"] + _INLINE_SPAN_ROWS + [
        "Instructions",
        "Whisk the egg whites until frothy.",
        "Fold in the Parmesan and the seasonings.",
        "Cook in an oiled pan over medium heat.",
    ]


def test_the_inline_span_paste_imports_all_eight_ingredients():
    got = parse_pasted(INLINE_SPANS)

    assert got is not None
    assert [i["display"] for i in got["ingredients"]] == _INLINE_SPAN_ROWS


def test_the_inline_span_paste_through_the_endpoint_saves_eight_rows(auth_client,
                                                                    monkeypatch):
    """End to end on the real endpoint, with no provider: the whole point is
    that this markup no longer needs a model and no longer loses the amounts."""
    def no_provider():
        from app.services.ai.base import ProviderError
        raise ProviderError("none configured")
    monkeypatch.setattr(ai_api, "get_provider", no_provider)

    r = auth_client.post("/api/v1/ai/import", json={"text": INLINE_SPANS})

    assert r.status_code == 201
    rid = r.get_json()["id"]
    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"]
    assert [i["display"] for i in got] == _INLINE_SPAN_ROWS


def test_pretty_printed_markup_reads_the_same_as_minified():
    """Whitespace between tags is insignificant in HTML, so the indented
    spelling of the same list must not split where the minified one does not."""
    lines = html_to_lines(
        "<ul>\n  <li>\n    <span>240 grams</span>\n"
        "    <span>Egg Whites</span>\n  </li>\n</ul>").splitlines()

    assert lines == ["240 grams Egg Whites"]


@pytest.mark.parametrize("html,want", [
    # A space IS needed: two word characters meet, and without it you get
    # "240 gramsEgg Whites" — the corruption the text path had to repair.
    ("<li><span>240 grams</span><span>Egg Whites</span></li>",
     "240 grams Egg Whites"),
    # A space is NOT needed: a bracket already reads as a boundary, and this
    # keeps the output identical to the text segmenter's.
    ("<li><span>Egg Whites</span><span>(240 g)</span></li>", "Egg Whites(240 g)"),
    # A fraction split across three elements must stay one token.
    ("<li><sup>1</sup><span>⁄</span><sub>2</sub> cup milk</li>", "1⁄2 cup milk"),
    ("<li><sup>¹</sup>⁄<sub>₂</sub> cup milk</li>", "¹⁄₂ cup milk"),
    # A hyphen inside a brand name must not gain spaces around it.
    ("<li>1 packet Jell<span>-</span>O</li>", "1 packet Jell-O"),
    # <wbr> is a break OPPORTUNITY, not a space.
    ("<li>Worcester<wbr>shire sauce</li>", "Worcestershire sauce"),
    # <br> is inline but it really is a line break.
    ("<div>2 cups flour<br>1 cup milk</div>", "2 cups flour\n1 cup milk"),
    # Whitespace already present in the source is never doubled.
    ("<li><b>2 cups</b> <span>flour</span></li>", "2 cups flour"),
    # Non-Latin scripts are alphanumeric too, so they space like any other word.
    ("<li><span>Мука</span><span>200 г</span></li>", "Мука 200 г"),
    ("<li><span>小麦粉</span><span>200グラム</span></li>", "小麦粉 200グラム"),
    # DECOMPOSED accents: a macOS clipboard sends "café" as e + U+0301, and a
    # combining mark is not alnum, so testing the last code unit naively gave
    # "caféBeans" here while the precomposed spelling got its space.
    ("<li><span>café</span><span>Beans</span></li>",
     "café Beans"),
    ("<li><span>café</span><span>Beans</span></li>", "café Beans"),
    # A decimal point splits across elements on some sites and must not gain
    # spaces — "1 . 5 kg" would lose the quantity entirely.
    ("<li><span>1</span><span>.</span><span>5</span> kg beef</li>",
     "1.5 kg beef"),
    # An ABBREVIATED unit ends in a period — the US recipe-site norm — and a
    # period is not alnum, so this stayed glued as "2 tsp.Vanilla extract".
    ("<li><span>2 tsp.</span><span>Vanilla extract</span></li>",
     "2 tsp. Vanilla extract"),
    ("<li><span>1 lb.</span><span>Ground beef</span></li>", "1 lb. Ground beef"),
    # Metric-FIRST layouts put the bracketed amount before the name, so a
    # closing bracket has to end a token too.
    ("<li><span>(240 g)</span><span>Egg Whites</span></li>",
     "(240 g) Egg Whites"),
    ("<li><span>70%</span><span>Dark Chocolate</span></li>", "70% Dark Chocolate"),
    ("<li><span>flour,</span><span>sifted</span></li>", "flour, sifted"),
    # A comma is the decimal separator across most of Europe, so like "." it
    # ends a token only after a LETTER — "1, 5 kg" would lose the quantity.
    ("<li><span>1,</span><span>5 kg</span> beef</li>", "1,5 kg beef"),
])
def test_the_inline_join_whitespace_rule(html, want):
    assert html_to_lines(html) == want


def test_a_block_element_still_breaks_the_line():
    """The fix joins INLINE elements only. If a block stopped breaking, an
    ingredient list would collapse into one run-on line again."""
    assert html_to_lines(
        "<div><p>2 cups flour</p><p>1 cup milk</p>"
        "<table><tr><td>2 eggs</td></tr><tr><td>1 tsp salt</td></tr></table>"
        "<dl><dt>Butter</dt><dd>225g</dd></dl></div>").splitlines() == [
        "2 cups flour", "1 cup milk", "2 eggs", "1 tsp salt", "Butter", "225g"]


def test_an_unknown_custom_tag_still_breaks_the_line():
    """Unrecognised elements default to BLOCK, which is the pre-fix behaviour
    for everything — so a custom tag can never silently swallow the next line."""
    assert html_to_lines(
        "<wprm-row>2 cups flour</wprm-row><wprm-row>1 cup milk</wprm-row>"
    ).splitlines() == ["2 cups flour", "1 cup milk"]


def test_a_preformatted_block_keeps_its_own_line_breaks():
    """<pre> is the one place the source's newlines are significant; collapsing
    them would weld a whole method into a single line."""
    assert html_to_lines(
        "<pre>2 cups flour\n1 cup milk\n2 eggs</pre>").splitlines() == [
        "2 cups flour", "1 cup milk", "2 eggs"]


@pytest.mark.parametrize("html,want", [
    # A <pre> is WALKED like any other element, not flattened with get_text():
    # taking its text wholesale skipped <br>, the inline-join rule, nested
    # blocks and the synthetic markers, welding "240 gramsEgg Whites" — worse
    # than the bug being fixed.
    ("<pre>2 cups flour<br>1 cup milk</pre>", ["2 cups flour", "1 cup milk"]),
    ("<pre><span>240 grams</span><span>Egg Whites</span></pre>",
     ["240 grams Egg Whites"]),
    ("<pre><div>2 cups flour</div><div>1 cup milk</div></pre>",
     ["2 cups flour", "1 cup milk"]),
    ('<pre><div class="recipe-ingredients">2 cups flour</div></pre>',
     ["Ingredients", "2 cups flour"]),
])
def test_a_preformatted_block_is_walked_not_flattened(html, want):
    assert html_to_lines(html).splitlines() == want


def test_newline_preservation_ends_with_the_preformatted_block():
    """The pre region has to be EXITED. Leaking it makes every later block
    preserve newlines too, so ordinary source wrapping starts splitting lines
    that a browser renders as one."""
    lines = html_to_lines(
        "<div><pre>2 cups flour\n1 cup milk</pre>"
        "<p>Mix the dry ingredients\n   together well.</p></div>").splitlines()

    assert lines == ["2 cups flour", "1 cup milk",
                     "Mix the dry ingredients together well."]


# The clipboard's text/html flavour of a copied PLAIN-TEXT recipe is one
# element carrying white-space:pre-wrap, and RecipeImport.vue posts that
# flavour in preference to the plain one. Collapsing those newlines made the
# whole paste a single line, so nothing parsed and the user hit the "no AI
# provider" dead end this module exists to avoid.
PRE_WRAP_PASTE = (
    "<meta charset='utf-8'><span style='white-space:pre-wrap'>Chocolate Cake\n"
    "Ingredients\n2 cups flour\n1 cup sugar\n3 eggs\nInstructions\n"
    "Mix the dry ingredients together well.\nBake for 25 minutes and cool.</span>")


def test_a_white_space_pre_wrap_paste_keeps_its_newlines():
    assert html_to_lines(PRE_WRAP_PASTE).splitlines() == [
        "Chocolate Cake", "Ingredients", "2 cups flour", "1 cup sugar",
        "3 eggs", "Instructions",
        "Mix the dry ingredients together well.", "Bake for 25 minutes and cool."]


def test_a_white_space_pre_wrap_paste_still_parses_without_a_provider():
    got = parse_pasted(PRE_WRAP_PASTE)

    assert got is not None
    assert [i["display"] for i in got["ingredients"]] == [
        "2 cups flour", "1 cup sugar", "3 eggs"]
    assert [s["text"] for s in got["steps"]] == [
        "Mix the dry ingredients together well.", "Bake for 25 minutes and cool."]


def test_a_bolded_lead_in_heading_still_ends_the_method():
    """"<p><strong>Notes</strong> Keep refrigerated…</p>" is mainstream
    recipe-blog markup. Joining the bold into its paragraph is typographically
    right but destroys the heading as a LINE — which is what _TAIL_HEAD matches
    on — so "Notes" stopped terminating the method and came back as a step."""
    got = parse_pasted(
        '<p><strong>Ingredients</strong></p>'
        '<ul><li>2 cups flour</li><li>1 cup milk</li></ul>'
        '<p><strong>Instructions</strong></p>'
        '<ol><li>Mix well.</li><li>Bake 25 min.</li></ol>'
        '<p><strong>Notes</strong> Keep refrigerated for up to three days.</p>')

    assert [s["text"] for s in got["steps"]] == ["Mix well.", "Bake 25 min."]


def test_a_bolded_sub_heading_still_becomes_a_section():
    """Same mechanism, the other casualty: "For the sauce:" bolded inline
    stopped being a section and corrupted an ingredient's display text."""
    lines = html_to_lines(
        '<ul class="ingredients"><li>2 cups flour</li></ul>'
        '<p><strong>For the sauce:</strong> 2 tbsp soy sauce</p>'
        '<ol class="instructions"><li>Mix it all together well.</li></ol>'
    ).splitlines()

    assert "For the sauce:" in lines
    assert "2 tbsp soy sauce" in lines


@pytest.mark.parametrize("html", [
    "<p><strong>Notes</strong> Keep it cold.</p>",
    "<p>\n  <strong>Notes</strong> Keep it cold.</p>",     # pretty-printed
    "<p> <strong>Notes</strong> Keep it cold.</p>",
    "<p>&nbsp;<strong>Notes</strong> Keep it cold.</p>",
])
def test_a_lead_in_heading_isolates_however_the_source_is_indented(html):
    """"At the start of the block" means no VISIBLE text yet, not an empty
    buffer — pretty-printed markup puts "\\n  " in the buffer first, so testing
    for empty missed every indented page, which is most of them."""
    assert html_to_lines(html).splitlines() == ["Notes", "Keep it cold."]


def test_a_heading_word_mid_sentence_is_not_a_lead_in():
    """Only a heading that STARTS the block gets its own line; "See Notes
    below." is one sentence and splitting it would invent a section."""
    assert html_to_lines("<p>See <strong>Notes</strong> below.</p>") == (
        "See Notes below.")


def test_a_bolded_amount_is_not_mistaken_for_a_heading():
    """The lead-in rule must not re-split an ordinary ingredient row: only the
    parser's own four heading patterns count, and an amount matches none."""
    assert html_to_lines(
        "<li><b>240 grams</b><span>Egg Whites</span></li>") == "240 grams Egg Whites"


def test_a_long_bolded_sentence_ending_in_a_colon_is_not_a_heading():
    """_SUB_HEAD matches anything ending in a colon, so the lead-in rule is
    length-capped the same way _is_sub_heading is — prose stays prose."""
    long_lead = ("Before you start, read this whole method through twice and "
                 "get everything out of the fridge first:")
    assert html_to_lines(f"<p><strong>{long_lead}</strong> Then begin.</p>") == (
        f"{long_lead} Then begin.")


@pytest.mark.parametrize("html,want", [
    # <img> is INLINE. WordPress rewrites emoji in post content to
    # <img class="wp-smiley">, and unit/diet icons sit inside ingredient rows,
    # so this split "2 cups" from "flour" — the headline bug by another route.
    ("<ul><li>2 cups <img src=i.png> flour</li><li>1 cup milk</li></ul>",
     ["2 cups flour", "1 cup milk"]),
    ("<li><span>240 grams</span><img src=i.png><span>Egg Whites</span></li>",
     ["240 grams Egg Whites"]),
    ("<li>2 cups <picture><source srcset=i.webp><img src=i.png></picture> flour</li>",
     ["2 cups flour"]),
])
def test_a_replaced_inline_element_does_not_break_the_line(html, want):
    assert html_to_lines(html).splitlines() == want


@pytest.mark.parametrize("html", [
    # A working browser renders NONE of this fallback copy, so joining it into
    # the row ("2 cups Your browser cannot play video. flour") is worse than
    # the junk line it replaced. Inline, but the subtree is skipped.
    "<li>2 cups <video><source src=x>Your browser cannot play video.</video> flour</li>",
    "<li>2 cups <object>no plugin available</object> flour</li>",
    "<li>2 cups <iframe>frames are not supported</iframe> flour</li>",
    "<li>2 cups <meter value=1>1 of 1</meter> flour</li>",
    # MathML's ~30 token elements are not inline either, so a walked formula
    # split one line per token. Treated as opaque instead.
    "<li>2 cups <math><mn>1</mn><mo>+</mo><mn>2</mn></math> flour</li>",
])
def test_replaced_element_fallback_text_never_enters_the_line(html):
    assert html_to_lines(html) == "2 cups flour"


def test_an_ingredient_row_with_an_icon_still_resolves_its_amount():
    """Through the parser, not just the flattener: the icon used to split the
    row so the amount arrived as an ingredient of its own."""
    got = parse_pasted(
        '<h1>Thing</h1><ul class="ingredients">'
        '<li><img src=i.png><span>240 grams</span><span>Egg Whites</span></li>'
        '<li><img src=i.png><span>40 grams</span><span>Parmesan</span></li></ul>'
        '<ol class="instructions"><li>Mix it all together well.</li></ol>')

    assert [i["display"] for i in got["ingredients"]] == [
        "240 grams Egg Whites", "40 grams Parmesan"]


# --- Parity with the pre-fix flattener on bolded-heading shapes --------------
#
# These three were raised in review as new defects. They are NOT: the pre-fix
# `soup.get_text("\n")` produced byte-identical lines and byte-identical parses
# for all three, because it split at EVERY string boundary and so also gave a
# bolded lead-in its own line. They are locked down here as parity tests so the
# distinction is recorded and a future change has to be deliberate.
#
# The "Tip:" case does lose method steps, but via _until_tail treating a
# mid-method "Tip:" as a terminator — a pre-existing parser defect reachable
# from plain text with no HTML involved ("Method\n1. Mix.\nTip:\n..."), and
# out of scope for a flattener fix. See the Noticed list in the commit.

def test_a_bolded_lead_in_keeps_its_own_line_as_it_always_did():
    """The flattener's contract is the LINE shape, and this is unchanged from
    the pre-fix behaviour for every bolded-lead-in shape."""
    assert html_to_lines(
        '<h2>Instructions</h2><ol><li>Mash the bananas well.</li></ol>'
        '<p><strong>Tip:</strong> Use very ripe bananas for a sweeter loaf.</p>'
    ).splitlines() == [
        "Instructions", "Mash the bananas well.",
        "Tip:", "Use very ripe bananas for a sweeter loaf."]


def test_a_definition_style_row_keeps_its_pre_fix_reading():
    """"Flour:" has always become a section rather than part of the display —
    `_SUB_HEAD` matches any line ending in a colon and the pre-fix flattener
    put it on its own line too."""
    got = parse_pasted(
        '<h1>Thing</h1><ul class="ingredients">'
        '<li><span>Flour:</span><span>240 g</span></li>'
        '<li><span>Butter:</span><span>100 g</span></li></ul>'
        '<ol class="instructions"><li>Mix it all together well.</li></ol>')

    assert [(i["display"], i["section"]) for i in got["ingredients"]] == [
        ("240 g", "Flour"), ("100 g", "Butter")]


def test_text_after_a_block_does_not_weld_onto_it():
    """A block ends its line when its content ENDS, not merely when the next
    one starts. Flushing only on entry let loose text following a <p> join it:
    "2 cups flour" + "and a pinch of salt" became one ingredient."""
    assert html_to_lines(
        "<div><p>2 cups flour</p>and a pinch of salt</div>").splitlines() == [
        "2 cups flour", "and a pinch of salt"]


def test_nested_inline_elements_keep_their_word_order():
    """An inline element with several children must be read left to right. Get
    the traversal order wrong and "2 cups sifted flour" comes out reversed as
    "flour sifted 2 cups" — still one line, so line-count assertions miss it."""
    assert html_to_lines(
        "<li><b>2 cups <i>sifted</i> flour</b></li>") == "2 cups sifted flour"


def test_deeply_nested_markup_does_not_blow_the_stack():
    """bs4 walks the tree iteratively, so get_text coped with any nesting. A
    RECURSIVE flattener raised RecursionError at ~1000 levels (measured) —
    which this function must not do, because the URL-import path feeds it whole
    pages nobody vetted and a 500 there is worse than the bug being fixed."""
    deep = "<div>" * 3000 + "2 cups flour" + "</div>" * 3000

    assert html_to_lines(deep).splitlines() == ["2 cups flour"]


def test_a_synthetic_heading_does_not_land_in_a_neighbours_line():
    """The marker used to be the string "\\nIngredients\\n", which only worked
    while every string boundary broke a line. Next to an inline sibling it
    merged: "12 muffins" immediately before a labelled list became
    "12 muffins Ingredients"."""
    lines = html_to_lines(
        '<div><span>12 muffins</span>'
        '<ul class="recipe-ingredients"><li>2 cups flour</li></ul></div>'
    ).splitlines()

    assert lines == ["12 muffins", "Ingredients", "2 cups flour"]

# --- a bolded terminator must never split onto its own line ----------------

def test_a_mid_method_tip_does_not_truncate_the_method():
    """REGRESSION, and the reason _is_lead_in_heading only matches REGION
    headings. A bolded "Tip:" between two step lists is the commonest recipe
    blog shape there is. When a terminator pattern was allowed to split a
    lead-in onto its own line, _until_tail cut the body there and three of four
    steps vanished — with the whole suite green, which is why this asserts
    through parse_pasted() and not html_to_lines(): the line-level output looks
    entirely plausible while the parse is losing user data.
    """
    html = (
        "<h2>Ingredients</h2><ul><li>2 cups flour</li><li>1 cup milk</li>"
        "<li>3 eggs</li></ul>"
        "<h2>Instructions</h2><ol><li>Mix well.</li></ol>"
        "<p><strong>Tip:</strong> Use very ripe bananas for the best flavour.</p>"
        "<ol><li>Bake for 25 minutes.</li><li>Cool on a rack and serve.</li></ol>"
    )

    got = parse_pasted(html)

    steps = [s["text"] if isinstance(s, dict) else s for s in got["steps"]]
    assert len(steps) == 4, f"method truncated: {steps}"
    assert steps[0] == "Mix well."
    assert steps[-1] == "Cool on a rack and serve."


def test_other_bolded_terminators_are_equally_harmless():
    for lead in ("Pro tip:", "Note:", "Substitutions:"):
        html = ("<h2>Ingredients</h2><ul><li>2 cups flour</li>"
                "<li>1 cup milk</li><li>3 eggs</li></ul>"
                "<h2>Instructions</h2><ol><li>Mix well.</li></ol>"
                f"<p><strong>{lead}</strong> Something advisory here.</p>"
                "<ol><li>Bake for 25 minutes.</li></ol>")

        got = parse_pasted(html)

        steps = [s["text"] if isinstance(s, dict) else s for s in got["steps"]]
        assert "Bake for 25 minutes." in steps, f"{lead!r} truncated: {steps}"


@pytest.mark.xfail(reason="STILL OPEN, and a DIFFERENT mechanism from the "
                          "_until_tail truncation it was filed alongside: "
                          "_SUB_HEAD matches anything ending in a colon, so a "
                          "definition-style row's food name is promoted to a "
                          "section and the display keeps only the amount. "
                          "_until_tail now distinguishes a run-in label from a "
                          "section heading, but this row is not a tail heading "
                          "at all — it never reaches that code. Fixing it means "
                          "teaching _is_sub_heading that a colon-terminated "
                          "line followed by a bare amount is a row, which is a "
                          "separate rule and gets its own change rather than "
                          "being forced into this one.",
                   strict=True)
def test_a_definition_style_row_keeps_its_food_in_the_display():
    """`<span>Flour:</span><span>240 g</span>` is a row, not a heading plus a
    row. Treating the colon-terminated span as a sub-heading left the display
    as "240 g" with the food name promoted to a section — an ingredient with no
    ingredient in it."""
    html = ("<ul class='recipe-ingredients'><li><span>Flour:</span>"
            "<span>240 g</span></li><li><span>Milk:</span>"
            "<span>500 ml</span></li></ul>"
            "<h2>Instructions</h2><ol><li>Mix them together well.</li></ol>")

    got = parse_pasted(html)

    displays = [i["display"] if isinstance(i, dict) else i
                for i in got["ingredients"]]
    assert any("Flour" in d for d in displays), displays


# --- a run-in label vs a section heading -------------------------------------
#
# The same words head two opposite things, so these come in pairs: the shape
# that must keep its body, and the shape that must still lose it. Each is
# asserted from PLAIN TEXT as well as HTML, because the rule lives in the text
# parser and a fix that only worked on markup would leave the other path broken.

_ING_TEXT = "Ingredients\n2 cups flour\n1 cup milk\n3 eggs\n\n"


def test_a_mid_method_tip_does_not_truncate_the_method_in_plain_text():
    """The plain-text twin of test_a_mid_method_tip_does_not_truncate_the_method.

    Not markup-specific: with no HTML whatsoever, a "Tip:" between two runs of
    steps used to take three of the four steps with it.
    """
    text = (_ING_TEXT + "Instructions\nMix well.\nTip:\nUse very ripe bananas.\n"
            "Bake for 25 minutes.\nCool on a rack and serve.")

    got = parse_recipe_text(text)

    steps = [s["text"] for s in got["steps"]]
    assert steps == ["Mix well.", "Use very ripe bananas.",
                     "Bake for 25 minutes.", "Cool on a rack and serve."]


def test_an_imperative_tip_is_still_only_a_tip():
    """The advisory text of a tip is itself an imperative sentence, so "does
    this read like an instruction" cannot be the signal — "Use very ripe
    bananas" and "Bake for 25 minutes" are the same grammatical shape. What
    separates them is the colon on the label, not the mood of the prose."""
    text = (_ING_TEXT + "Method\n1. Mash the bananas.\n"
            "Note:\nSift the flour first, it makes a lighter crumb.\n"
            "2. Bake for 50 minutes.")

    got = parse_recipe_text(text)

    steps = [s["text"] for s in got["steps"]]
    assert "Bake for 50 minutes." in steps, steps
    assert "Sift the flour first, it makes a lighter crumb." in steps, steps


def test_a_multi_sentence_notes_block_still_ends_the_method():
    """A real Notes block runs to several sentences, and those sentences talk
    about chilling, freezing and serving — so they carry cooking cue words. The
    bare heading is what keeps them out, which is why the colon gate is checked
    before anything else."""
    text = (_ING_TEXT + "Method\n1. Mash the bananas.\n2. Bake for 50 minutes.\n"
            "Notes\nKeep refrigerated for up to three days.\n"
            "These freeze beautifully for a month.\n"
            "Serve warm with ice cream.")

    got = parse_recipe_text(text)

    assert [s["text"] for s in got["steps"]] == [
        "Mash the bananas.", "Bake for 50 minutes."]


def test_a_multi_sentence_notes_block_still_ends_the_method_in_html():
    html = ("<h2>Ingredients</h2><ul><li>2 cups flour</li><li>1 cup milk</li>"
            "<li>3 eggs</li></ul>"
            "<h2>Instructions</h2><ol><li>Mash the bananas.</li>"
            "<li>Bake for 50 minutes.</li></ol>"
            "<h3>Notes</h3><p>Keep refrigerated for up to three days.</p>"
            "<p>These freeze beautifully for a month.</p>"
            "<p>Serve warm with ice cream.</p>")

    got = parse_pasted(html)

    assert [s["text"] for s in got["steps"]] == [
        "Mash the bananas.", "Bake for 50 minutes."]


def test_a_mid_method_tip_survives_a_later_real_notes_block():
    """Both shapes in one document, which is the common blog layout. The tip
    must not cut, and the Notes heading after it must."""
    text = (_ING_TEXT + "Instructions\nMix well.\nTip:\nUse very ripe bananas.\n"
            "Bake for 25 minutes.\n"
            "Notes\nBest eaten within two days.")

    got = parse_recipe_text(text)

    assert [s["text"] for s in got["steps"]] == [
        "Mix well.", "Use very ripe bananas.", "Bake for 25 minutes."]


def test_a_colon_label_with_no_method_after_it_still_ends_the_method():
    """The colon is necessary but not sufficient: a labelled trailing block with
    nothing step-like after it is still a trailing block."""
    text = (_ING_TEXT + "Method\n1. Mash the bananas.\n2. Bake for 50 minutes.\n"
            "Notes:\nBest eaten within two days.")

    got = parse_recipe_text(text)

    assert [s["text"] for s in got["steps"]] == [
        "Mash the bananas.", "Bake for 50 minutes."]


def test_an_ambiguous_tail_label_keeps_its_content_rather_than_losing_it():
    """The deliberate trade, pinned so it is a decision and not a surprise.

    "Notes:" with a cue-bearing sentence under it is genuinely ambiguous — it
    is shaped exactly like a mid-method aside. The rule keeps the content, so
    the cost of being wrong here is one stray row the user can see and delete,
    rather than a method that silently lost most of its steps. If this ever
    needs to flip, the asymmetry in that sentence is the thing to argue with.
    """
    text = (_ING_TEXT + "Method\n1. Mash the bananas.\n2. Bake for 50 minutes.\n"
            "Notes:\nChill for an hour before slicing.")

    got = parse_recipe_text(text)

    steps = [s["text"] for s in got["steps"]]
    assert "Chill for an hour before slicing." in steps, steps
    assert "Notes:" not in steps, "the label itself is a heading, not a step"
