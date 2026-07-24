"""Photo → recipe (vision import, feature #9): provider surface + endpoint."""
import io

import pytest

import app.api.ai as ai_api
from app.services.ai.base import AIProvider, ChatResult, ProviderError
from app.services.ai.recipe_import import recipe_from_image


class VisionProvider(AIProvider):
    name = "fake"

    def __init__(self, payload):
        self.payload = payload

    def available(self):
        return True

    def _complete(self, system, prompt, max_tokens):
        return "{}"

    def chat(self, messages, system="", tools=None, max_tokens=2048):
        return ChatResult(content="ok")

    def complete_json_image(self, prompt, image_b64, media_type, system="", max_tokens=4096):
        return self.payload


# --- provider surface -------------------------------------------------------

def test_base_provider_rejects_images_by_default():
    class NoVision(VisionProvider):
        complete_json_image = AIProvider.complete_json_image  # use the base impl

    with pytest.raises(ProviderError, match="does not support image"):
        NoVision({})._complete_image("s", "p", "b64", "image/png", 10)


def test_recipe_from_image_normalizes_payload():
    p = VisionProvider({"name": "Scanned Cake",
                        "ingredients": [{"display": "2 eggs"}],
                        "steps": [{"text": "Mix"}]})
    out = recipe_from_image("b64", "image/png", p)
    assert out["name"] == "Scanned Cake"
    assert out["ingredients"][0]["display"] == "2 eggs"


# --- endpoint ---------------------------------------------------------------

def _upload(client, mimetype="image/jpeg", content=b"\xff\xd8\xffdata"):
    return client.post(
        "/api/v1/ai/photo",
        data={"image": (io.BytesIO(content), "photo.jpg", mimetype)},
        content_type="multipart/form-data",
    )


def test_photo_endpoint_saves_recipe_and_keeps_image(auth_client, monkeypatch):
    monkeypatch.setattr(ai_api, "get_provider", lambda: VisionProvider({
        "name": "Grandma's Soup",
        "ingredients": [{"display": "1 onion"}, {"display": "2 carrots"}],
        "steps": [{"text": "Chop"}, {"text": "Simmer"}],
    }))
    r = _upload(auth_client)
    assert r.status_code == 201
    body = r.get_json()
    assert body["name"] == "Grandma's Soup"
    assert len(body["ingredients"]) == 2
    assert body["image"]  # the uploaded photo is kept as the recipe image


def test_photo_endpoint_rejects_unsupported_type(auth_client, monkeypatch):
    monkeypatch.setattr(ai_api, "get_provider", lambda: VisionProvider({}))
    assert _upload(auth_client, mimetype="image/gif").status_code == 422


def test_photo_endpoint_422_when_no_recipe_found(auth_client, monkeypatch):
    monkeypatch.setattr(ai_api, "get_provider",
                        lambda: VisionProvider({"name": "", "ingredients": [], "steps": []}))
    assert _upload(auth_client).status_code == 422


def test_photo_endpoint_422_when_provider_has_no_vision(auth_client, monkeypatch):
    class NoVision(VisionProvider):
        def complete_json_image(self, *a, **k):
            raise ProviderError("this AI provider does not support image input")

    monkeypatch.setattr(ai_api, "get_provider", lambda: NoVision({}))
    r = _upload(auth_client)
    # A non-vision provider is a config mismatch, not an upstream failure.
    assert r.status_code == 422
    assert "image" in r.get_json()["error"]
