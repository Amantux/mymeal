from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin

recipe_tags = db.Table(
    "recipe_tags",
    db.Column("recipe_id", String(36), ForeignKey("recipes.id"), primary_key=True),
    db.Column("tag_id", String(36), ForeignKey("tags.id"), primary_key=True),
)


class Tag(IDMixin, TimestampMixin, db.Model):
    """A free-form recipe tag, e.g. "vegetarian", "quick", "gluten-free"."""

    __tablename__ = "tags"

    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), index=True)
    color: Mapped[str] = mapped_column(String(16), default="")

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="tags")

    recipes = relationship("Recipe", secondary=recipe_tags, back_populates="tags")
