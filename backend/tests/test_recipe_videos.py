"""How-to videos on recipes: links, uploads, and the boundaries around them."""
import io

import pytest

from app.services.videos import VideoError, embed_url, normalize_url, video_mime


def _recipe(auth_client, name="Test recipe"):
    return auth_client.post("/api/v1/recipes", json={"name": name}).get_json()["id"]


def _mp4(name="clip.mp4"):
    # A real player is not involved; the server decides by extension, on purpose.
    return {"file": (io.BytesIO(b"\x00\x00\x00\x20ftypisom fake"), name)}


# --- link validation -------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "vbscript:msgbox",
    "notaurl",
    "",
    # These carry a netloc, so the "does it look like a web address" check
    # passes them and ONLY the scheme allowlist rejects them. Without one of
    # these the parametrize was vacuous: deleting the scheme check left every
    # other case still failing on the empty netloc.
    "javascript://example.com/%0aalert(1)",
    "data://example.com/x",
    "jAvAsCrIpT://example.com/%0aalert(1)",
])
def test_a_non_http_link_is_refused(auth_client, bad):
    """These end up in an href. A scheme allowlist, not a blocklist, so a
    scheme nobody has thought of yet is refused by not being listed."""
    rid = _recipe(auth_client)

    r = auth_client.post(f"/api/v1/recipes/{rid}/videos", json={"url": bad})

    assert r.status_code == 422


def test_credentials_are_stripped_from_a_pasted_link():
    assert normalize_url("https://user:pw@youtu.be/abc") == "https://youtu.be/abc"


def test_a_link_is_stored_and_returned(auth_client):
    rid = _recipe(auth_client)

    r = auth_client.post(f"/api/v1/recipes/{rid}/videos",
                    json={"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Technique"})

    assert r.status_code == 201
    body = r.get_json()
    assert body["title"] == "Technique"
    assert body["url"] == "https://youtu.be/dQw4w9WgXcQ"
    assert body["streamUrl"] is None      # a link has nothing to stream


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=abc123", "https://www.youtube-nocookie.com/embed/abc123"),
    ("https://youtu.be/abc123", "https://www.youtube-nocookie.com/embed/abc123"),
    ("https://vimeo.com/123456789", "https://player.vimeo.com/video/123456789"),
])
def test_known_providers_get_an_embed_url(url, expected):
    assert embed_url(url) == expected


def test_an_unknown_host_is_not_embeddable():
    """The server decides what may be framed; the UI never builds an embed URL
    from a raw address."""
    assert embed_url("https://example.com/clip.mp4") is None


@pytest.mark.parametrize("hostile", [
    "https://youtu.be/abc/../../evil?x=1",
    "https://youtu.be/abc?a=b",
    "https://www.youtube.com/watch?v=abc/../evil",
])
def test_a_provider_id_cannot_redirect_the_iframe(hostile):
    """An id carrying / or ? would point the frame somewhere else on the
    provider's domain. Asserts the PROPERTY — the id contains nothing that can
    change the target — rather than a hand-computed expected string."""
    out = embed_url(hostile)

    if out is None:
        return
    prefix = "https://www.youtube-nocookie.com/embed/"
    assert out.startswith(prefix)
    video_id = out[len(prefix):]
    assert all(c.isalnum() or c in "-_" for c in video_id)


# --- uploads ---------------------------------------------------------------

def test_an_upload_is_stored_and_streamable(auth_client):
    rid = _recipe(auth_client)

    created = auth_client.post(f"/api/v1/recipes/{rid}/videos", data=_mp4(),
                          content_type="multipart/form-data").get_json()

    assert created["streamUrl"] is not None
    assert created["url"] is None
    played = auth_client.get(created["streamUrl"].replace("/api/v1", "/api/v1"))
    assert played.status_code == 200
    assert played.headers["Content-Type"].startswith("video/mp4")


def test_a_non_video_upload_is_refused(auth_client):
    """Nothing stops a user uploading anything; the point is that the server
    never agrees to serve it back inline."""
    rid = _recipe(auth_client)

    r = auth_client.post(f"/api/v1/recipes/{rid}/videos",
                    data={"file": (io.BytesIO(b"<html>hi"), "payload.html")},
                    content_type="multipart/form-data")

    assert r.status_code == 422


def test_the_served_content_type_comes_from_the_allowlist_not_the_filename(auth_client):
    """A file called clip.mp4 full of HTML must still be served as video/mp4,
    so it cannot execute in the app's origin."""
    rid = _recipe(auth_client)
    created = auth_client.post(f"/api/v1/recipes/{rid}/videos",
                          data={"file": (io.BytesIO(b"<html><script>x</script>"), "clip.mp4")},
                          content_type="multipart/form-data").get_json()

    played = auth_client.get(created["streamUrl"])

    assert played.headers["Content-Type"].startswith("video/mp4")


def test_a_range_request_is_answered_so_the_player_can_seek(auth_client):
    rid = _recipe(auth_client)
    created = auth_client.post(f"/api/v1/recipes/{rid}/videos", data=_mp4(),
                          content_type="multipart/form-data").get_json()

    r = auth_client.get(created["streamUrl"], headers={"Range": "bytes=0-3"})

    assert r.status_code == 206
    assert r.headers["Content-Range"].startswith("bytes 0-3/")


@pytest.mark.parametrize("name", ["a.mp4", "a.WEBM", "a.mov"])
def test_known_video_extensions_are_playable(name):
    assert video_mime(name)


def test_an_unknown_extension_is_not_playable():
    assert video_mime("a.html") is None


# --- the invariant ---------------------------------------------------------

def test_a_video_cannot_be_both_a_file_and_a_link():
    from app.models import RecipeVideo
    from app.services import videos

    with pytest.raises(VideoError):
        videos.validate(RecipeVideo(recipe_id="r", url="https://x/y", filename="a.mp4"))


def test_a_video_must_be_one_or_the_other():
    from app.models import RecipeVideo
    from app.services import videos

    with pytest.raises(VideoError):
        videos.validate(RecipeVideo(recipe_id="r"))


# --- listing, deleting, tenancy -------------------------------------------

def test_videos_are_listed_in_order_and_on_the_recipe(auth_client):
    rid = _recipe(auth_client)
    for i in range(3):
        auth_client.post(f"/api/v1/recipes/{rid}/videos",
                    json={"url": f"https://example.com/{i}", "title": f"v{i}"})

    listed = auth_client.get(f"/api/v1/recipes/{rid}/videos").get_json()
    embedded = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["videos"]

    assert [v["title"] for v in listed] == ["v0", "v1", "v2"]
    assert len(embedded) == 3


def test_deleting_a_video_removes_it(auth_client):
    rid = _recipe(auth_client)
    vid = auth_client.post(f"/api/v1/recipes/{rid}/videos",
                      json={"url": "https://example.com/x"}).get_json()["id"]

    assert auth_client.delete(f"/api/v1/recipes/{rid}/videos/{vid}").status_code == 204
    assert auth_client.get(f"/api/v1/recipes/{rid}/videos").get_json() == []


def test_a_video_from_another_recipe_is_not_reachable(auth_client):
    """The id is checked against the recipe we already tenant-scoped."""
    mine = _recipe(auth_client, "mine")
    other = _recipe(auth_client, "other")
    vid = auth_client.post(f"/api/v1/recipes/{other}/videos",
                      json={"url": "https://example.com/x"}).get_json()["id"]

    assert auth_client.get(f"/api/v1/recipes/{mine}/videos/{vid}/stream").status_code == 404
    assert auth_client.delete(f"/api/v1/recipes/{mine}/videos/{vid}").status_code == 404


def test_restoring_an_older_version_keeps_the_videos(auth_client):
    """Recipe versions snapshot content in the update endpoint's shape. Videos
    are a child table, not a field that endpoint accepts, so _apply — which is
    key-guarded — must leave them alone. Pinned because the reverse mistake (a
    new field missing from the snapshot builder, and silently wiped on restore)
    has bitten this repo before.
    """
    rid = _recipe(auth_client, "Soup")
    auth_client.put(f"/api/v1/recipes/{rid}", json={"description": "v2"})
    auth_client.post(f"/api/v1/recipes/{rid}/videos",
                     json={"url": "https://youtu.be/abc123", "title": "How to"})
    versions = auth_client.get(f"/api/v1/recipes/{rid}/versions").get_json()
    assert versions, "expected an auto-snapshot to exist"
    version_id = (versions[0] if isinstance(versions, list) else versions["items"][0])["id"]

    auth_client.post(f"/api/v1/recipes/{rid}/versions/{version_id}/restore")

    after = auth_client.get(f"/api/v1/recipes/{rid}").get_json()["videos"]
    assert [v["title"] for v in after] == ["How to"]
