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
