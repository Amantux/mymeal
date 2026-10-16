"""Media attached to a recipe — photos and how-to videos.

A separate table rather than a column on Recipe: a recipe can have several
(a technique clip, the original creator's walkthrough, a photo of the finished
dish, your own attempt), and they are ordered.

Distinct from ``Recipe.image``, which is the ONE hero thumbnail the card and
list views render. That is an identity field with a single value; this is a
gallery that grows. Folding them together would mean either "the first media
item is the thumbnail" (surprising to reorder) or a flag on one row, and would
drag the list/card serializers into this table.

Two shapes share the table and exactly one applies to a row:

* a **link** — ``url`` holds an external address, nothing is stored on disk;
* an **upload** — ``filename`` names a file under ``<DATA_DIR>/videos``.

``kind`` says whether the row is a ``video`` or an ``image``. It is a stored
column, not derived from the filename or URL, because for a link there is
nothing reliable to derive it from: ``https://youtu.be/xyz`` and
``https://i.example.com/abc`` both carry no extension and mean different
things. Deriving would also make the type guessable for uploads but not for
links — one rule for both is why the column exists. Rows written before the
column existed are videos, which is what its server default says.

Kind also decides which shapes are legal: an image must be an upload. The app's
CSP is ``img-src 'self' data: blob:``, so an external image URL would render as
a broken icon rather than a photo — refusing it with a sentence beats shipping
a feature the browser blocks.

The invariants are enforced in ``services.videos.validate`` so a bad request is
a 422 with a sentence rather than an IntegrityError surfacing as a 500.
"""
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class RecipeVideo(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "recipe_videos"

    title: Mapped[str] = mapped_column(String(255), default="")

    # "video" | "image". String, not an Enum: SQLite has no native enum and a
    # Postgres enum needs its own migration to gain a member, which is a steep
    # price for a two-value set that may grow (audio, pdf).
    kind: Mapped[str] = mapped_column(
        String(16), default="video", server_default="video"
    )

    # Exactly one of these is set.
    url: Mapped[str] = mapped_column(String(2048), default="", server_default="")
    filename: Mapped[str] = mapped_column(String(255), default="", server_default="")

    # Manual ordering, so the clip you actually follow can be first.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    recipe_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recipes.id"), index=True
    )
    recipe = relationship("Recipe", back_populates="videos")

    # Denormalised for the same reason every other table carries it: every query
    # filters by tenant, and joining through the recipe to do so is how a
    # cross-tenant read gets written by accident.
    group_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("groups.id"), index=True, nullable=True
    )
