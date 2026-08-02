"""How-to videos attached to a recipe.

A separate table rather than a column on Recipe: a recipe can have several
(a technique clip, the original creator's walkthrough, your own attempt), and
they are ordered.

Two shapes share the table and exactly one applies to a row:

* a **link** — ``url`` holds an external address, nothing is stored on disk;
* an **upload** — ``filename`` names a file under ``<DATA_DIR>/videos``.

The invariant is enforced in ``services.videos.validate`` so a bad request is a
422 with a sentence rather than an IntegrityError surfacing as a 500.
"""
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class RecipeVideo(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "recipe_videos"

    title: Mapped[str] = mapped_column(String(255), default="")

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
