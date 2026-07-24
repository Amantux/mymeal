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


def test_ingredients_parsed_to_structured_on_save(auth_client):
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Parsed", "ingredients": [
            {"display": "2 cups flour"}, {"display": "salt to taste"}],
    }).get_json()["id"]
    ings = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"]
    assert ings[0]["quantity"] == 2 and ings[0]["unit"]["name"] == "cup"
    assert ings[1]["quantity"] == 0 and ings[1]["unit"] is None  # unparseable → left blank


def test_ai_parse_ingredients_endpoint(auth_client, monkeypatch):
    import app.api.ai as ai_api

    class _P:
        def complete_json(self, prompt, system=""):
            return {"ingredients": [
                {"display": "2 cups flour", "quantity": 2, "unit": "cup",
                 "food": "flour", "note": "sifted"}]}

    monkeypatch.setattr(ai_api, "get_provider", lambda: _P())
    r = auth_client.post("/api/v1/ai/parse-ingredients", json={"lines": ["2 cups flour"]})
    assert r.status_code == 200
    ing = r.get_json()["ingredients"][0]
    assert ing["quantity"] == 2 and ing["unit"] == "cup" and ing["food"] == "flour"


def test_structured_row_matches_unit_and_food_on_save(auth_client):
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Struct", "ingredients": [
            {"display": "2 cups flour", "quantity": 2, "unit": "cup", "food": "flour"}],
    }).get_json()["id"]
    ing = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["ingredients"][0]
    assert ing["quantity"] == 2
    assert ing["unit"]["name"] == "cup"
    assert ing["food"]["name"] == "flour"


def test_recipe_scaling_and_weight_view(auth_client):
    rid = auth_client.post("/api/v1/recipes", json={
        "name": "Pancakes", "servings": 2,
        "ingredients": [{"display": "2 cups flour"}, {"display": "2 eggs"}],
    }).get_json()["id"]

    base = auth_client.get(f"/api/v1/recipes/{rid}").get_json()
    assert [i["display"] for i in base["ingredients"]] == ["2 cups flour", "2 eggs"]

    scaled = auth_client.get(f"/api/v1/recipes/{rid}?servings=4").get_json()
    assert scaled["scaledServings"] == 4
    assert [i["display"] for i in scaled["ingredients"]] == ["4 cup flour", "4 eggs"]

    weight = auth_client.get(f"/api/v1/recipes/{rid}?units=weight").get_json()
    dishes = [i["display"] for i in weight["ingredients"]]
    assert dishes[0].endswith("g flour")  # cup flour → grams via density
    assert dishes[1] == "2 eggs"          # no unit/density → unchanged


def test_create_recipe_tool_requires_ingredients_and_steps(app):
    gid = _group(app)
    with app.app_context():
        res = execute_tool(gid, "create_recipe", {"name": "Empty", "ingredients": [], "steps": []})
        assert "error" in res
        assert db.session.query(Recipe).filter_by(name="Empty").count() == 0


# --- import: image download (SSRF guard, type/size caps, best-effort) --------
import os  # noqa: E402
import threading  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402

from app.api.recipes import download_image_to_recipe  # noqa: E402


@contextmanager
def _image_server(content_type, body):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/pic"
    finally:
        srv.shutdown()


def _recipe(app):
    g = Group(name="H")
    db.session.add(g)
    db.session.flush()
    r = Recipe(name="R", slug="r", group_id=g.id)
    db.session.add(r)
    db.session.flush()
    return r


def test_image_download_rejects_private_host_best_effort(app):
    # Real guard: a private host must be refused and NOT raise (best-effort).
    with app.app_context():
        r = _recipe(app)
        download_image_to_recipe(r, "http://127.0.0.1:1/x.jpg")
        assert r.image == ""


def test_image_download_swallows_invalid_url(app, monkeypatch):
    # Guard bypassed; an invalid port raises httpx.InvalidURL (not HTTPError) —
    # must be swallowed, never 500 the import.
    monkeypatch.setattr("app.services.ai.recipe_import._assert_public_url", lambda u: None)
    with app.app_context():
        r = _recipe(app)
        download_image_to_recipe(r, "http://127.0.0.1:notaport/x.jpg")
        assert r.image == ""


def test_image_download_success_saves_file(app, monkeypatch):
    monkeypatch.setattr("app.services.ai.recipe_import._assert_public_url", lambda u: None)
    with app.app_context():
        r = _recipe(app)
        with _image_server("image/png", b"\x89PNG\r\n\x1a\n" + b"x" * 100) as url:
            download_image_to_recipe(r, url)
        assert r.image == f"{r.id}.png"
        assert os.path.isfile(os.path.join(app.config["SETTINGS"].images_dir, r.image))


def test_image_download_rejects_non_image_content_type(app, monkeypatch):
    monkeypatch.setattr("app.services.ai.recipe_import._assert_public_url", lambda u: None)
    with app.app_context():
        r = _recipe(app)
        with _image_server("text/html", b"<html>not an image</html>") as url:
            download_image_to_recipe(r, url)
        assert r.image == ""


def test_image_download_respects_size_cap(app, monkeypatch):
    monkeypatch.setattr("app.services.ai.recipe_import._assert_public_url", lambda u: None)
    monkeypatch.setattr("app.api.recipes._MAX_IMAGE_BYTES", 50)
    with app.app_context():
        r = _recipe(app)
        with _image_server("image/png", b"x" * 500) as url:
            download_image_to_recipe(r, url)
        assert r.image == ""
