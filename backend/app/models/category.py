from sqlalchemy import String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin

# Association tables between recipes and their categories / tags.
recipe_categories = db.Table(
    "recipe_categories",
    db.Column("recipe_id", String(36), ForeignKey("recipes.id"), primary_key=True),
    db.Column(
        "category_id", String(36), ForeignKey("categories.id"), primary_key=True
    ),
)


class Category(IDMixin, TimestampMixin, db.Model):
    """A recipe category, e.g. "Dinner", "Dessert", "Breakfast"."""

    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="categories")

    recipes = relationship(
        "Recipe", secondary=recipe_categories, back_populates="categories"
    )
