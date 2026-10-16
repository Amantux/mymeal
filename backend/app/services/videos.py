"""Recipe media: link validation, the attachment invariants, and safe serving.

myMeal has no attachment system to extend — a recipe has a single ``image``
column (its hero thumbnail) and nothing else — so recipe media gets its own
small table. Photos and videos share it; ``kind`` says which a row is.

The module keeps its ``videos`` name: every internal reference, the table and
the on-disk directory are already called that, and renaming them would be a
wide diff for no behaviour change. The name users and API clients see is
"media".

Four rules live here, in one place, because every write path needs all of them:

1. A stored link is **http(s) only**. The server never fetches this URL; the
   browser renders it, so the risk is a ``javascript:`` or ``data:`` address
   becoming an ``href``. This is deliberately NOT
   ``services.ai.url_guard.llm_url_ok`` — that is an SSRF guard which
   intentionally permits loopback and private LAN so a local Ollama works, which
   is the opposite of what is wanted here.
2. An attachment is either a file or a link, and belongs to exactly one parent.
3. Only a known media type is ever served **inline**; everything else keeps
   downloading, so a user-uploaded file can never be rendered as active content.
4. An **image must be an upload**. The app's CSP is
   ``img-src 'self' data: blob:``, so an external image address cannot render
   in the page at all — storing one would ship a guaranteed broken image.
   Refusing it with a sentence is the honest answer, and the alternative
   (widening ``img-src`` to any origin) would spend the only CSP myMeal has on
   a feature nobody asked for.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

# Served inline; anything else downloads. The Content-Type sent to the browser
# comes from THIS map, never from the uploaded filename — that is what stops a
# file called "clip.mp4" full of HTML from executing in the app's origin.
VIDEO_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}

# Images follow the same rule as videos: the Content-Type the browser is given
# comes from THIS map keyed on OUR generated extension, never from the
# uploaded file's client-supplied mimetype. SVG is deliberately absent — an
# SVG is a document that can carry script, and served inline from the app's
# own origin it would execute there.
IMAGE_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
}

KIND_VIDEO = "video"
KIND_IMAGE = "image"
KINDS = (KIND_VIDEO, KIND_IMAGE)

MAX_URL_LENGTH = 2048
MAX_TITLE_LENGTH = 255


class VideoError(ValueError):
    """Invalid media input. Carries a message safe to return to the caller."""


# The public name. `VideoError` stays as an alias because it is raised and
# caught by name in several modules and in the test suite.
MediaError = VideoError


def normalize_kind(raw) -> str:
    """The stored `kind` for a caller-supplied value.

    Falsy means video: that is what every row written before the column
    existed is, and what an unspecified `kind` has always meant on this
    endpoint. Anything else unrecognised is a 422 rather than a silent
    coercion — a typo'd kind would otherwise be stored as a video and render
    a photo in a `<video>` element.
    """
    value = (raw or KIND_VIDEO).strip().lower()
    if value not in KINDS:
        raise VideoError(f"kind must be one of {', '.join(KINDS)}")
    return value


def normalize_url(raw: str) -> str:
    """Validate and normalize an external video link.

    Returns the cleaned URL, or raises :class:`VideoError` with a message that
    says what to do — the caller turns it into a 422.
    """
    value = (raw or "").strip()
    if not value:
        raise VideoError("a video link is required")
    if len(value) > MAX_URL_LENGTH:
        raise VideoError(f"that link is longer than {MAX_URL_LENGTH} characters")

    parsed = urlparse(value)
    # Scheme allowlist, not a blocklist: "javascript", "data", "vbscript",
    # "file" and anything invented later are all rejected by not being listed.
    if parsed.scheme.lower() not in ("http", "https"):
        raise VideoError("the link must start with http:// or https://")
    if not parsed.netloc:
        raise VideoError("that does not look like a web address")
    # Strip any credentials someone pasted from a private link; they would end
    # up rendered in an href and stored in the database.
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    return urlunparse(parsed._replace(scheme=parsed.scheme.lower(), netloc=netloc))


def video_mime(filename: str) -> str | None:
    """The inline Content-Type for this filename, or None if it is not a video."""
    return VIDEO_MIME_BY_EXT.get(os.path.splitext(filename or "")[1].lower())


def image_mime(filename: str) -> str | None:
    """The inline Content-Type for this filename, or None if it is not an image."""
    return IMAGE_MIME_BY_EXT.get(os.path.splitext(filename or "")[1].lower())


def media_mime(kind: str, filename: str) -> str | None:
    """The inline Content-Type for a stored media file, or None.

    Keyed on the row's `kind` rather than searching both maps, so a row stored
    as an image can never be served with a video Content-Type (or the reverse)
    even if someone hand-edits a filename in the database.
    """
    if normalize_kind(kind) == KIND_IMAGE:
        return image_mime(filename)
    return video_mime(filename)


def upload_ext(kind: str, filename: str) -> str | None:
    """The extension we will store this upload under, or None if the kind does
    not accept that file type.

    The decision is made on the extension of the name the client sent, and the
    answer is an extension from OUR map — so the stored name is always one we
    minted and `media_mime` is guaranteed to recognise it later.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    table = (IMAGE_MIME_BY_EXT if normalize_kind(kind) == KIND_IMAGE
             else VIDEO_MIME_BY_EXT)
    return ext if ext in table else None


def accepted_extensions(kind: str) -> str:
    """The extensions this kind accepts, for an error message."""
    table = (IMAGE_MIME_BY_EXT if normalize_kind(kind) == KIND_IMAGE
             else VIDEO_MIME_BY_EXT)
    return ", ".join(sorted(table))


def validate(media) -> None:
    """The one-file-or-one-link invariant, plus uploads-only for images.

    Raises :class:`VideoError`. Enforced here rather than by a database
    constraint so a bad request is a 422 with a sentence, not an
    IntegrityError surfacing as a 500.
    """
    kind = normalize_kind(getattr(media, "kind", None))
    has_url = bool((media.url or "").strip())
    has_file = bool((media.filename or "").strip())
    if has_url == has_file:
        raise VideoError(
            f"a {kind} must be either an uploaded file or a link, not both"
            if kind == KIND_VIDEO else
            "an image must be an uploaded file")
    if kind == KIND_IMAGE and has_url:
        # See rule 4 in the module docstring: the CSP means an external image
        # address cannot render, so accepting one would store a broken photo.
        raise VideoError(
            "photos have to be uploaded, not linked — this app only displays "
            "images it stores itself. Upload the file, or add it as a video link "
            "if it is a clip.")
    if not media.recipe_id:
        raise VideoError(f"a {kind} must belong to a recipe")


def embed_url(url: str) -> str | None:
    """A same-page embed URL for the providers we allow framing, else None.

    Only YouTube and Vimeo are recognised. The allowlist is the control here:
    myMeal ships no Content-Security-Policy (unlike its sibling add-ons), so
    nothing else would stop an arbitrary URL being framed — which is exactly why
    the decision of what may be embedded is made HERE rather than by handing the
    UI any URL and letting it build an iframe. Anything unrecognised is rendered
    as a plain link that opens in a new tab.
    """
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return None
    host = parsed.netloc.lower().removeprefix("www.")
    if host in ("youtube.com", "m.youtube.com"):
        # ?v=ID
        for part in parsed.query.split("&"):
            key, _, value = part.partition("=")
            if key == "v" and value:
                return f"https://www.youtube-nocookie.com/embed/{_safe_id(value)}"
        return None
    if host == "youtu.be":
        vid = parsed.path.lstrip("/")
        return f"https://www.youtube-nocookie.com/embed/{_safe_id(vid)}" if vid else None
    if host == "vimeo.com":
        vid = parsed.path.lstrip("/").split("/")[0]
        return f"https://player.vimeo.com/video/{_safe_id(vid)}" if vid.isdigit() else None
    return None


def _safe_id(value: str) -> str:
    """Keep only characters a provider id can contain.

    The result is interpolated into a URL the page will frame, so a value
    carrying ``/`` or ``?`` could otherwise point the iframe somewhere else on
    the provider's domain.
    """
    return "".join(c for c in value if c.isalnum() or c in "-_")[:64]
