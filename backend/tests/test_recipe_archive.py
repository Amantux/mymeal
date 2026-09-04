"""Bulk import from other recipe managers' exports — the migration path.

Fixtures are built in-test as REAL archives (zip-of-gzip for Paprika, nested
zips for Tandoor) so the walking/guard code runs for real, not against stubs.
"""
import gzip
import io
import json
import zipfile

from app.services.recipe_archive import (
    MAX_ENTRY_BYTES,
    extract_payloads,
    parse_mealie,
    parse_paprika,
    parse_tandoor,
)

PAPRIKA = {
    "name": "Weeknight Chili",
    "servings": "4 servings",
    "prep_time": "15 min",
    "cook_time": "45 min",
    "ingredients": "1 tbsp olive oil\n2 tins tomatoes\n1 onion, diced",
    "directions": "1. Fry the onion.\n2. Add the tomatoes and simmer.",
    "notes": "Freezes well.",
    "categories": ["Dinner", "Batch cook"],
    "source_url": "https://example.com/chili",
}

TANDOOR = {
    "name": "Pancakes",
    "servings": 4,
    "working_time": 10,
    "waiting_time": 20,
    "keywords": [{"name": "breakfast"}],
    "steps": [
        {"name": "", "instruction": "Whisk everything together.",
         "ingredients": [
             {"amount": 2.0, "unit": {"name": "cups"}, "food": {"name": "flour"},
              "note": "sifted"},
             {"amount": 0, "unit": None, "food": {"name": "salt"}, "note": ""},
         ]},
        {"name": "Cook", "instruction": "Fry until golden.", "ingredients": []},
    ],
}

MEALIE = {
    "name": "Garden Salad",
    "recipeServings": 2,
    "prepTime": "PT10M",
    "recipeIngredient": [
        {"display": "1 head lettuce, torn", "food": {"name": "lettuce"}},
        {"quantity": 2, "unit": {"name": "tbsp"}, "food": {"name": "olive oil"},
         "note": "extra virgin", "display": ""},
        "a handful of croutons",
    ],
    "recipeInstructions": [{"text": "Toss everything."}, {"text": "Dress and serve."}],
    "tags": [{"name": "fresh"}],
    "orgURL": "https://example.com/salad",
}


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


# --- Format parsers ----------------------------------------------------------

def test_paprika_maps_newline_blobs_to_rows_and_steps():
    got = parse_paprika(PAPRIKA)

    assert got["name"] == "Weeknight Chili"
    assert got["servings"] == 4
    assert (got["prepMinutes"], got["cookMinutes"]) == (15, 45)
    assert [i["display"] for i in got["ingredients"]] == [
        "1 tbsp olive oil", "2 tins tomatoes", "1 onion, diced"]
    assert [s["text"] for s in got["steps"]] == [
        "Fry the onion.", "Add the tomatoes and simmer."]
    assert got["notes"] == "Freezes well."
    assert got["sourceUrl"] == "https://example.com/chili"


def test_tandoor_lifts_ingredients_out_of_the_steps():
    got = parse_tandoor(TANDOOR)

    assert [i["display"] for i in got["ingredients"]] == [
        "2 cups flour, sifted", "salt"]
    assert [s["text"] for s in got["steps"]] == [
        "Whisk everything together.", "Fry until golden."]
    assert got["steps"][1]["title"] == "Cook"
    assert got["servings"] == 4
    assert got["tags"] == ["breakfast"]


def test_mealie_prefers_the_display_line_and_rebuilds_when_absent():
    got = parse_mealie(MEALIE)

    assert [i["display"] for i in got["ingredients"]] == [
        "1 head lettuce, torn",
        "2 tbsp olive oil, extra virgin",
        "a handful of croutons",
    ]
    assert got["servings"] == 2
    assert got["prepMinutes"] == 10
    assert got["sourceUrl"] == "https://example.com/salad"


# --- Archive walking ---------------------------------------------------------

def test_a_paprikarecipes_archive_is_zip_of_gzipped_json():
    blob = _zip({
        "Weeknight Chili.paprikarecipe": gzip.compress(json.dumps(PAPRIKA).encode()),
    })

    payloads, skipped = extract_payloads("export.paprikarecipes", blob)

    assert [p["name"] for p in payloads] == ["Weeknight Chili"]
    assert skipped == []


def test_a_tandoor_export_is_a_zip_of_zips():
    inner = _zip({"recipe.json": json.dumps(TANDOOR).encode()})
    blob = _zip({"1.zip": inner})

    payloads, skipped = extract_payloads("tandoor-export.zip", blob)

    assert [p["name"] for p in payloads] == ["Pancakes"]
    assert skipped == []


def test_a_mealie_zip_and_sibling_images_import_without_the_images():
    blob = _zip({
        "recipes/garden-salad/garden-salad.json": json.dumps(MEALIE).encode(),
        "recipes/garden-salad/images/original.webp": b"\x00fakeimage",
    })

    payloads, skipped = extract_payloads("mealie.zip", blob)

    assert [p["name"] for p in payloads] == ["Garden Salad"]
    assert skipped == []          # images are silently fine, not "skipped" noise


def test_a_bare_schema_org_json_goes_through_the_shared_normalizer():
    node = {"@type": "Recipe", "name": "LD Cake",
            "recipeIngredient": ["2 cups flour", "3 eggs"],
            "recipeInstructions": [{"@type": "HowToStep", "text": "Bake it."}]}

    payloads, _ = extract_payloads("recipe.json", json.dumps(node).encode())

    assert payloads[0]["name"] == "LD Cake"
    assert [i["display"] for i in payloads[0]["ingredients"]] == [
        "2 cups flour", "3 eggs"]


def test_a_json_list_imports_each_recipe():
    blob = json.dumps([MEALIE, dict(MEALIE, name="Second Salad")]).encode()

    payloads, _ = extract_payloads("mealie-all.json", blob)

    assert [p["name"] for p in payloads] == ["Garden Salad", "Second Salad"]


def test_a_plain_text_entry_uses_the_deterministic_parser():
    text = ("Toast\nIngredients\n2 slices bread\n1 tbsp butter\n"
            "Method\n1. Toast the bread.\n2. Butter it.\n")
    blob = _zip({"toast.txt": text.encode()})

    payloads, _ = extract_payloads("notes.zip", blob)

    assert [i["display"] for i in payloads[0]["ingredients"]] == [
        "2 slices bread", "1 tbsp butter"]


def test_broken_entries_are_skipped_with_reasons_never_fatal():
    blob = _zip({
        "good.paprikarecipe": gzip.compress(json.dumps(PAPRIKA).encode()),
        "not-gzip.paprikarecipe": b"plainly not gzip",
        "not-json.json": b"{{{{",
        "mystery.xyz": b"???",
        "not-a-recipe.json": json.dumps({"hello": "world"}).encode(),
    })

    payloads, skipped = extract_payloads("mixed.paprikarecipes", blob)

    assert [p["name"] for p in payloads] == ["Weeknight Chili"]
    reasons = {s["entry"]: s["reason"] for s in skipped}
    assert "not-gzip.paprikarecipe" in reasons
    assert "not-json.json" in reasons
    assert "mystery.xyz" in reasons
    assert "not-a-recipe.json" in reasons


def test_an_oversized_entry_is_skipped_not_read():
    big = zipfile.ZipInfo("huge.json")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(big, b"x" * (MAX_ENTRY_BYTES + 100))
        zf.writestr("ok.json", json.dumps(MEALIE).encode())

    payloads, skipped = extract_payloads("big.zip", buf.getvalue())

    assert [p["name"] for p in payloads] == ["Garden Salad"]
    assert any("limit" in s["reason"] for s in skipped)


def test_nested_zips_stop_at_one_level():
    """Tandoor needs depth 1; depth 2 is a zip-bomb shape, not an export."""
    level2 = _zip({"recipe.json": json.dumps(TANDOOR).encode()})
    level1 = _zip({"nested.zip": level2})
    blob = _zip({"outer.zip": level1})

    payloads, skipped = extract_payloads("deep.zip", blob)

    assert payloads == []
    assert skipped   # the too-deep zip is reported, not silently eaten


# --- Through the endpoint ----------------------------------------------------

def test_bulk_import_creates_recipes_and_reports_skips(auth_client):
    blob = _zip({
        "chili.paprikarecipe": gzip.compress(json.dumps(PAPRIKA).encode()),
        "broken.paprikarecipe": b"not gzip",
    })

    r = auth_client.post(
        "/api/v1/ai/import/archive",
        data={"archive": (io.BytesIO(blob), "export.paprikarecipes")},
        content_type="multipart/form-data")

    assert r.status_code == 201
    body = r.get_json()
    assert body["createdCount"] == 1
    assert body["created"][0]["name"] == "Weeknight Chili"
    assert body["skippedCount"] == 1

    # And the recipe is really there, structured.
    rid = body["created"][0]["id"]
    got = auth_client.get(f"/api/v1/recipes/{rid}").get_json()
    assert len(got["ingredients"]) == 3
    assert len(got["steps"]) == 2
    assert got["servings"] == 4


def test_an_empty_or_recipe_free_file_is_a_422_with_reasons(auth_client):
    r = auth_client.post(
        "/api/v1/ai/import/archive",
        data={"archive": (io.BytesIO(_zip({"readme.md": b"hello"})), "x.zip")},
        content_type="multipart/form-data")

    assert r.status_code == 422
    assert "no recipes" in r.get_json()["error"]


def test_no_model_and_no_network_is_needed(auth_client, monkeypatch):
    """The migration path must work on a box with nothing configured. Poison the
    provider registry AND httpx to prove neither is touched."""
    import app.api.ai as ai_api

    def boom(*a, **k):
        raise AssertionError("bulk import touched the network or the model")
    monkeypatch.setattr(ai_api, "get_provider", boom)
    import httpx
    monkeypatch.setattr(httpx, "request", boom)
    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx, "post", boom)

    blob = _zip({"r.paprikarecipe": gzip.compress(json.dumps(PAPRIKA).encode())})
    r = auth_client.post(
        "/api/v1/ai/import/archive",
        data={"archive": (io.BytesIO(blob), "export.paprikarecipes")},
        content_type="multipart/form-data")

    assert r.status_code == 201 and r.get_json()["createdCount"] == 1
