"""The recipe media slot: photos alongside videos in one ordered gallery.

`test_recipe_videos.py` is the companion suite and is deliberately unchanged —
it exercises the pre-media `/videos` paths and the `videos`/`streamUrl` keys,
so it is the regression test for "the old contract still works". This file
covers what `kind` adds.
"""
import io

import pytest

from app.services.videos import (
    KINDS,
    VideoError,
    image_mime,
    media_mime,
    normalize_kind,
    upload_ext,
)


def _recipe(auth_client, name="Test recipe"):
    return auth_client.post("/api/v1/recipes", json={"name": name}).get_json()["id"]


def _png(name="dish.png", body=b"\x89PNG\r\n\x1a\n fake"):
    # A real decoder is not involved; the server decides by extension, on
    # purpose — the stored name and served type are both ours.
    return {"file": (io.BytesIO(body), name)}


# --- kind ------------------------------------------------------------------

def test_an_unspecified_kind_is_a_video():
    """What every row written before the column existed is, and what the
    endpoint has always meant by an absent kind."""
    assert normalize_kind(None) == "video"
    assert normalize_kind("") == "video"


@pytest.mark.parametrize("bad", ["audio", "photo", "VIDEOS", "../image"])
def test_an_unrecognised_kind_is_refused_not_coerced(bad):
    """A typo'd kind silently stored as a video would render a photo in a
    <video> element."""
    with pytest.raises(VideoError):
        normalize_kind(bad)


def test_a_bogus_kind_on_the_endpoint_is_a_422(auth_client):
    rid = _recipe(auth_client)

    r = auth_client.post(f"/api/v1/recipes/{rid}/media",
                         json={"url": "https://youtu.be/abc", "kind": "audio"})

    assert r.status_code == 422


# --- image uploads ---------------------------------------------------------

def test_an_uploaded_photo_is_stored_as_an_image(auth_client):
    rid = _recipe(auth_client)

    created = auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                               content_type="multipart/form-data").get_json()

    assert created["kind"] == "image"
    assert created["fileUrl"] is not None
    assert created["url"] is None


def test_the_kind_is_derived_from_the_extension_when_not_given(auth_client):
    """So the UI can offer one "add a photo or video" picker instead of asking
    the user to classify their own file."""
    rid = _recipe(auth_client)

    photo = auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                             content_type="multipart/form-data").get_json()
    clip = auth_client.post(
        f"/api/v1/recipes/{rid}/media",
        data={"file": (io.BytesIO(b"\x00\x00\x00\x20ftypisom"), "clip.mp4")},
        content_type="multipart/form-data").get_json()

    assert photo["kind"] == "image"
    assert clip["kind"] == "video"


def test_an_uploaded_photo_is_served_back(auth_client):
    rid = _recipe(auth_client)
    created = auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                               content_type="multipart/form-data").get_json()

    shown = auth_client.get(created["fileUrl"])

    assert shown.status_code == 200
    assert shown.headers["Content-Type"].startswith("image/png")


def test_the_served_photo_content_type_comes_from_the_allowlist_not_the_file(auth_client):
    """A file called dish.png full of HTML must still be served as image/png,
    so it cannot execute in the app's origin."""
    rid = _recipe(auth_client)
    created = auth_client.post(
        f"/api/v1/recipes/{rid}/media",
        data=_png(body=b"<html><script>alert(1)</script>"),
        content_type="multipart/form-data").get_json()

    shown = auth_client.get(created["fileUrl"])

    assert shown.headers["Content-Type"].startswith("image/png")


@pytest.mark.parametrize("name", ["payload.svg", "payload.html", "payload.svgz",
                                  "payload", "payload.mp4.html"])
def test_a_file_on_neither_allowlist_is_refused(auth_client, name):
    """SVG is the pointed one: it is an image to a user but a document that can
    carry script, and served inline from our own origin it would execute
    there."""
    rid = _recipe(auth_client)

    r = auth_client.post(f"/api/v1/recipes/{rid}/media",
                         data={"file": (io.BytesIO(b"<svg onload=alert(1)>"), name)},
                         content_type="multipart/form-data")

    assert r.status_code == 422


def test_a_declared_image_must_actually_be_an_image_file(auth_client):
    """An explicit kind does not widen what that kind accepts."""
    rid = _recipe(auth_client)

    r = auth_client.post(
        f"/api/v1/recipes/{rid}/media",
        data={"file": (io.BytesIO(b"\x00ftyp"), "clip.mp4"), "kind": "image"},
        content_type="multipart/form-data")

    assert r.status_code == 422


def test_the_stored_filename_is_never_the_clients(auth_client, app):
    """Traversal and collision both come from trusting the uploaded name."""
    from app.models import RecipeVideo
    rid = _recipe(auth_client)

    auth_client.post(f"/api/v1/recipes/{rid}/media",
                     data=_png(name="../../../../etc/passwd.png"),
                     content_type="multipart/form-data")

    with app.app_context():
        stored = RecipeVideo.query.one().filename
    assert "/" not in stored and ".." not in stored
    assert stored.endswith(".png")


# --- images are uploads, not links ----------------------------------------

def test_an_image_link_is_refused(auth_client):
    """The CSP is img-src 'self' data: blob:, so an external image address
    renders as a broken icon. Refusing it beats storing a photo that cannot
    display."""
    rid = _recipe(auth_client)

    r = auth_client.post(f"/api/v1/recipes/{rid}/media",
                         json={"url": "https://example.com/dish.jpg",
                               "kind": "image"})

    assert r.status_code == 422
    assert "upload" in r.get_json()["error"].lower()


def test_an_image_link_is_refused_at_the_service_too():
    """The guard is in validate(), so every write path gets it — not just the
    one endpoint that exists today."""
    from app.models import RecipeVideo
    from app.services import videos

    with pytest.raises(VideoError):
        videos.validate(RecipeVideo(recipe_id="r", kind="image",
                                    url="https://example.com/x.jpg"))


def test_a_video_link_is_still_accepted(auth_client):
    """The refusal is specific to images; link-only video support is the
    feature that already existed."""
    rid = _recipe(auth_client)

    r = auth_client.post(f"/api/v1/recipes/{rid}/media",
                         json={"url": "https://youtu.be/dQw4w9WgXcQ"})

    assert r.status_code == 201
    assert r.get_json()["kind"] == "video"


# --- serving is keyed on the row's kind -----------------------------------

def test_an_image_row_cannot_be_served_with_a_video_type():
    """media_mime keys on the row's kind rather than searching both maps, so a
    hand-edited filename cannot flip the served type."""
    assert media_mime("image", "a.mp4") is None
    assert media_mime("video", "a.png") is None
    assert media_mime("image", "a.png") == "image/png"
    assert media_mime("video", "a.mp4") == "video/mp4"


@pytest.mark.parametrize("name", ["a.jpg", "a.JPEG", "a.png", "a.webp", "a.gif"])
def test_known_image_extensions_are_displayable(name):
    assert image_mime(name)


def test_svg_is_not_a_displayable_image():
    assert image_mime("a.svg") is None


def test_upload_ext_only_returns_an_extension_we_allow():
    assert upload_ext("image", "x.PNG") == ".png"
    assert upload_ext("image", "x.mp4") is None
    assert upload_ext("video", "x.mp4") == ".mp4"


# --- the gallery -----------------------------------------------------------

def test_photos_and_videos_share_one_ordered_slot(auth_client):
    rid = _recipe(auth_client)
    auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(name="a.png"),
                     content_type="multipart/form-data")
    auth_client.post(f"/api/v1/recipes/{rid}/media",
                     json={"url": "https://youtu.be/abc", "title": "How to"})

    listed = auth_client.get(f"/api/v1/recipes/{rid}/media").get_json()

    assert [m["kind"] for m in listed] == ["image", "video"]


def test_the_recipe_payload_carries_media_and_the_former_videos_key(auth_client):
    """Both keys are the same list: `media` is the name, `videos` is kept so a
    client written against it does not break."""
    rid = _recipe(auth_client)
    auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                     content_type="multipart/form-data")

    body = auth_client.get(f"/api/v1/recipes/{rid}").get_json()

    assert body["media"] == body["videos"]
    assert [m["kind"] for m in body["media"]] == ["image"]


def test_the_hero_image_is_untouched_by_media(auth_client):
    """Recipe.image is the card/list thumbnail — a different concept with a
    single value. Adding a photo to the gallery must not set it."""
    rid = _recipe(auth_client)
    auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                     content_type="multipart/form-data")

    body = auth_client.get(f"/api/v1/recipes/{rid}").get_json()

    assert body["image"] is None
    assert len(body["media"]) == 1


def test_deleting_a_photo_removes_the_row_and_the_file(auth_client, app):
    rid = _recipe(auth_client)
    created = auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                               content_type="multipart/form-data").get_json()

    assert auth_client.delete(
        f"/api/v1/recipes/{rid}/media/{created['id']}").status_code == 204

    assert auth_client.get(f"/api/v1/recipes/{rid}/media").get_json() == []
    assert auth_client.get(created["fileUrl"]).status_code == 404


def test_a_photo_from_another_recipe_is_not_reachable(auth_client):
    """The id is checked against the recipe we already tenant-scoped."""
    mine = _recipe(auth_client, "mine")
    other = _recipe(auth_client, "other")
    created = auth_client.post(f"/api/v1/recipes/{other}/media", data=_png(),
                               content_type="multipart/form-data").get_json()

    assert auth_client.get(
        f"/api/v1/recipes/{mine}/media/{created['id']}/file").status_code == 404
    assert auth_client.delete(
        f"/api/v1/recipes/{mine}/media/{created['id']}").status_code == 404


def test_media_requires_authentication(client):
    """Every other recipe route is login_required; the new paths are too."""
    assert client.get("/api/v1/recipes/whatever/media").status_code == 401


def test_every_kind_has_an_accepted_extension_list():
    """Guards against a kind being added to KINDS with no allowlist behind it,
    which would make every upload of that kind a 422 with an empty message."""
    from app.services import videos

    for kind in KINDS:
        assert videos.accepted_extensions(kind)


# --- regressions found in adversarial review ---------------------------------

def test_a_malformed_kind_does_not_break_the_library_listing(auth_client, app):
    """The serializer runs for every media row of every recipe, so a value it
    refuses takes out the whole list page rather than one tile. Read paths use
    display_kind (never raises); only input uses normalize_kind."""
    from app.extensions import db
    from app.models import RecipeVideo
    rid = _recipe(auth_client)
    auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                     content_type="multipart/form-data")
    with app.app_context():
        RecipeVideo.query.one().kind = "audio"   # only by hand-editing the DB
        db.session.commit()

    assert auth_client.get("/api/v1/recipes").status_code == 200
    assert auth_client.get(f"/api/v1/recipes/{rid}").status_code == 200

    shown = auth_client.get(f"/api/v1/recipes/{rid}/media").get_json()
    assert shown[0]["kind"] == "video", "an unknown kind should render as a video"


def test_an_unknown_kind_is_not_served_as_a_guess(auth_client, app):
    """The serving path degrades to 404, not to a mime found by searching both
    maps."""
    from app.extensions import db
    from app.models import RecipeVideo
    rid = _recipe(auth_client)
    created = auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                               content_type="multipart/form-data").get_json()
    with app.app_context():
        RecipeVideo.query.one().kind = "audio"
        db.session.commit()

    assert auth_client.get(created["fileUrl"]).status_code == 404


def test_deleting_a_recipe_removes_its_uploaded_media_files(auth_client, app):
    """The rows cascade away with the recipe, so this is the last moment
    anything knows the filename — a miss here is unreclaimable disk inside the
    user's Home Assistant backups."""
    import os

    from app.models import RecipeVideo
    rid = _recipe(auth_client)
    auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                     content_type="multipart/form-data")
    with app.app_context():
        stored = RecipeVideo.query.one().filename
        media_dir = app.config["videos_dir"]()
    assert os.path.isfile(os.path.join(media_dir, stored))  # arranged

    assert auth_client.delete(f"/api/v1/recipes/{rid}").status_code == 204

    assert not os.path.isfile(os.path.join(media_dir, stored))


@pytest.mark.parametrize("filename", ["我的照片.png", "café.png", "My Photo.png"])
def test_the_title_falls_back_to_the_readable_filename(auth_client, filename):
    """secure_filename strips non-ASCII, which titled a photo off a phone
    "png". The title is a display string and never touches the filesystem —
    the stored name is a uuid."""
    rid = _recipe(auth_client)

    created = auth_client.post(f"/api/v1/recipes/{rid}/media",
                               data=_png(name=filename),
                               content_type="multipart/form-data").get_json()

    assert created["title"] == filename


def test_a_declared_image_with_no_file_says_what_to_do(auth_client):
    """"a video link is required" told the user to supply the one thing an
    image may not have."""
    rid = _recipe(auth_client)

    r = auth_client.post(f"/api/v1/recipes/{rid}/media", json={"kind": "image"})

    assert r.status_code == 422
    assert "upload" in r.get_json()["error"].lower()


def test_the_legacy_stream_url_keeps_its_old_path(auth_client):
    """`streamUrl` is an alias for compatibility, so its VALUE has to stay the
    path it always was — a client that string-matched it would otherwise break
    even though the key survived."""
    rid = _recipe(auth_client)

    created = auth_client.post(f"/api/v1/recipes/{rid}/media", data=_png(),
                               content_type="multipart/form-data").get_json()

    assert created["streamUrl"].endswith("/stream")
    assert "/videos/" in created["streamUrl"]
    assert auth_client.get(created["streamUrl"]).status_code == 200


def test_media_from_another_household_is_not_reachable(auth_client, app):
    """The cross-RECIPE case was already covered; this is the cross-TENANT one
    the docstring claims. _get filters on group_id before _get_media runs."""
    mine = _recipe(auth_client, "mine")
    created = auth_client.post(f"/api/v1/recipes/{mine}/media", data=_png(),
                               content_type="multipart/form-data").get_json()

    other = app.test_client()
    other.post("/api/v1/users/register",
               json={"email": "b@b.com", "password": "password2", "name": "B"})
    token = other.post("/api/v1/users/login",
                       json={"username": "b@b.com", "password": "password2"}
                       ).get_json()["token"]
    other.environ_base["HTTP_AUTHORIZATION"] = token

    assert other.get(f"/api/v1/recipes/{mine}/media").status_code == 404
    assert other.get(
        f"/api/v1/recipes/{mine}/media/{created['id']}/file").status_code == 404
    assert other.delete(
        f"/api/v1/recipes/{mine}/media/{created['id']}").status_code == 404
