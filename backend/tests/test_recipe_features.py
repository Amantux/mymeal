"""Recipe import (images + tags) and the assistant's create_recipe tool."""
from app.extensions import db
from app.models import Group, Recipe
from app.services.ai.agent import execute_tool
from app.services.ai.recipe_import import _extract_tags, _first_image, normalize_jsonld


# --- import: image + tag extraction from schema.org JSON-LD ------------------

def test_jsonld_extracts_absolute_image_and_tags():
    node = {
        "@type": "Recipe", "name": "Soup",
        "recipeIngredient": ["1 onion"],
        "recipeInstructions": [{"@type": "HowToStep", "text": "Chop"}],
        "image": ["/img/soup.jpg"],
        "keywords": "soup, vegan, quick",
        "recipeCuisine": "French",
        "recipeCategory": ["Dinner"],
    }
    p = normalize_jsonld(node, "https://ex.com/recipes/soup")
    assert p["imageUrl"] == "https://ex.com/img/soup.jpg"  # relative resolved to absolute
    lowered = [t.lower() for t in p["tags"]]
    assert "soup" in lowered and "french" in lowered and "dinner" in lowered


def test_first_image_handles_string_list_and_imageobject():
    assert _first_image("http://x/a.jpg") == "http://x/a.jpg"
    assert _first_image([{"contentUrl": "http://x/b.png"}]) == "http://x/b.png"
    assert _first_image({"url": "http://x/c.webp"}) == "http://x/c.webp"
    assert _first_image(None) == ""


def test_extract_tags_dedupes_case_insensitively():
    node = {"keywords": ["Vegan", "vegan", "Quick"], "recipeCuisine": "Thai"}
    tags = _extract_tags(node)
    assert sum(t.lower() == "vegan" for t in tags) == 1
    assert "Thai" in tags


# --- assistant: create_recipe tool ------------------------------------------

def _group(app):
    with app.app_context():
        g = Group(name="Household")
        db.session.add(g)
        db.session.commit()
        return g.id


def test_create_recipe_tool_saves_recipe_with_tags(app):
    gid = _group(app)
    with app.app_context():
        res = execute_tool(gid, "create_recipe", {
            "name": "Test Chili", "servings": 4,
            "ingredients": ["1 can beans", "2 tomatoes"],
            "steps": ["Simmer 20 min", "Season"],
            "tags": ["dinner", "Dinner", "vegan"],  # dupe collapses
        })
        db.session.commit()
        assert res["created"] == "Test Chili"
        r = db.session.get(Recipe, res["recipeId"])
        assert [i.display for i in r.ingredients] == ["1 can beans", "2 tomatoes"]
        assert [s.text for s in r.steps] == ["Simmer 20 min", "Season"]
        assert sorted(t.name.lower() for t in r.tags) == ["dinner", "vegan"]
        assert r.servings == 4 and r.slug


def test_create_recipe_tool_requires_ingredients_and_steps(app):
    gid = _group(app)
    with app.app_context():
        res = execute_tool(gid, "create_recipe", {"name": "Empty", "ingredients": [], "steps": []})
        assert "error" in res
        assert db.session.query(Recipe).filter_by(name="Empty").count() == 0
