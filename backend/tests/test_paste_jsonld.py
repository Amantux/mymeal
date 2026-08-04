"""Pasting a schema.org Recipe directly.

Structured markup is already the shape the importer wants, so it must be parsed
deterministically — never sent to a model, and never blocked on having one
configured. The provider here is an Exploder: any call to it fails the test.
"""
import json

import pytest

import app.api.ai as ai_api
from app.services.ai.recipe_import import (
    UnsupportedPasteError,
    import_recipe,
    lint_payload,
)

RECIPE = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Cinnamon Roll Monkey Bread Focaccia",
    "description": "A soft, high-hydration focaccia-style dough.",
    "recipeYield": "12 servings",
    "prepTime": "PT35M",
    "cookTime": "PT32M",
    "totalTime": "PT3H15M",
    "recipeCategory": "Dessert",
    "recipeCuisine": "American",
    "keywords": "cinnamon roll, monkey bread, focaccia",
    "image": [],
    "recipeIngredient": ["500 g bread flour", "375 g whole milk, warm",
                         "25-40 g whole milk, for the glaze", "1 pinch fine salt"],
    "recipeInstructions": [
        {"@type": "HowToStep", "name": "Mix the dough",
         "text": "Combine the milk, yeast and sugar, then add the flour and salt."},
        {"@type": "HowToStep", "name": "Preheat and dimple",
         "text": "Preheat the oven to 175°C or 350°F, then dimple the dough."},
    ],
    "tool": [{"@type": "HowToTool", "name": "Kitchen scale"}],
    "suitableForDiet": "https://schema.org/VegetarianDiet",
}


class Exploder:
    def __getattr__(self, name):
        def fail(*a, **k):
            pytest.fail(f"provider.{name} was called — pasted JSON-LD must not "
                        f"reach the model")
        return fail


def test_pasted_jsonld_imports_with_no_provider_configured():
    """The whole point: structured markup does not need an AI provider."""
    p = import_recipe(text=json.dumps(RECIPE), provider=None)

    assert p["name"] == "Cinnamon Roll Monkey Bread Focaccia"
    assert p["servings"] == 12
    assert (p["prepMinutes"], p["cookMinutes"], p["totalMinutes"]) == (35, 32, 195)
    assert len(p["ingredients"]) == 4


def test_pasted_jsonld_never_reaches_the_model_even_when_one_exists():
    p = import_recipe(text=json.dumps(RECIPE), provider=Exploder())
    assert p["name"] == "Cinnamon Roll Monkey Bread Focaccia"


def test_howtostep_text_is_kept_and_the_name_becomes_the_title():
    """The bug this guards: _text() prefers a dict's `name` over its `text`,
    which is right for an ImageObject and catastrophic for a HowToStep — every
    instruction body was dropped and the recipe imported as bare headings."""
    p = import_recipe(text=json.dumps(RECIPE), provider=None)

    first = p["steps"][0]
    assert first["title"] == "Mix the dough"
    assert "Combine the milk" in first["text"], "the instruction body was lost"


def test_oven_temperature_is_read_out_of_the_step_text():
    """Follows from the above: the temperature scan reads step text, so while
    steps held only their titles it could never find a temperature."""
    p = import_recipe(text=json.dumps(RECIPE), provider=None)
    assert p["cookTemperatureC"] == pytest.approx(175.0)


def test_a_heading_identical_to_the_body_is_not_duplicated_as_a_title():
    node = dict(RECIPE, recipeInstructions=[
        {"@type": "HowToStep", "name": "Stir it well.", "text": "Stir it well."}])
    p = import_recipe(text=json.dumps(node), provider=None)
    assert p["steps"][0]["title"] == ""
    assert p["steps"][0]["text"] == "Stir it well."


def test_a_step_with_only_a_name_is_still_imported():
    """Sloppy markup should degrade, not vanish."""
    node = dict(RECIPE, recipeInstructions=[{"@type": "HowToStep", "name": "Just do it"}])
    p = import_recipe(text=json.dumps(node), provider=None)
    assert p["steps"][0]["text"] == "Just do it"


@pytest.mark.parametrize("wrapper", [
    lambda r: json.dumps(r),                                   # bare object
    lambda r: json.dumps([r]),                                 # array
    lambda r: json.dumps({"@graph": [{"@type": "WebSite"}, r]}),  # @graph
    lambda r: f'<script type="application/ld+json">{json.dumps(r)}</script>',
])
def test_the_shapes_people_actually_paste_all_work(wrapper):
    p = import_recipe(text=wrapper(RECIPE), provider=None)
    assert p["name"] == "Cinnamon Roll Monkey Bread Focaccia"


def test_an_empty_image_array_does_not_break_the_import():
    """`"image": []` is valid and common; _first_image must return "" not crash."""
    p = import_recipe(text=json.dumps(RECIPE), provider=None)
    assert p["imageUrl"] == ""


# --- the lint ---------------------------------------------------------------

def test_json_that_is_not_a_recipe_says_what_it_found(monkeypatch):
    with pytest.raises(UnsupportedPasteError) as ei:
        import_recipe(text='{"@type":"Article","name":"Not a recipe"}', provider=None)
    assert "Article" in str(ei.value)


def test_malformed_json_reports_where():
    with pytest.raises(UnsupportedPasteError) as ei:
        import_recipe(text='{"@type": "Recipe", "name": ', provider=None)
    msg = str(ei.value)
    assert "line 1" in msg and "column" in msg


def test_a_well_formed_recipe_lints_clean():
    p = import_recipe(text=json.dumps(RECIPE), provider=None)
    assert lint_payload(p) == []


def test_lint_names_the_thin_bits():
    thin = {"@context": "https://schema.org", "@type": "Recipe", "name": "Vague Soup",
            "recipeIngredient": ["some carrots", "a bit of stock"],
            "recipeInstructions": [{"@type": "HowToStep", "text": "Cook it."}]}
    warns = " ".join(lint_payload(import_recipe(text=json.dumps(thin), provider=None)))
    assert "serving" in warns
    assert "readable quantity" in warns and "some carrots" in warns
    assert "timings" in warns


# --- through the API --------------------------------------------------------

def test_pasting_through_the_endpoint_saves_the_recipe(auth_client, monkeypatch):
    from app.services.ai.base import ProviderError

    def no_provider():
        raise ProviderError("none configured")

    monkeypatch.setattr(ai_api, "get_provider", no_provider)

    r = auth_client.post("/api/v1/ai/import", json={"text": json.dumps(RECIPE)})

    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["name"] == "Cinnamon Roll Monkey Bread Focaccia"
    assert body["steps"][0]["title"] == "Mix the dough"
    assert body["warnings"] == []


def test_pasting_non_recipe_json_is_a_422_not_a_provider_503(auth_client, monkeypatch):
    """Reporting 'no AI provider configured' here sends people to the wrong
    settings page — the provider was never the problem."""
    from app.services.ai.base import ProviderError

    def no_provider():
        raise ProviderError("none configured")

    monkeypatch.setattr(ai_api, "get_provider", no_provider)

    r = auth_client.post("/api/v1/ai/import",
                         json={"text": '{"@type":"Article","name":"x"}'})

    assert r.status_code == 422
    assert "Recipe" in r.get_json()["error"]
