from sqlalchemy import String, Text, Integer, Float, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class Recipe(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "recipes"

    name: Mapped[str] = mapped_column(String(255), index=True)
    # Unique-per-group URL handle, e.g. "roast-chicken".
    slug: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    # Yield / servings. ``recipe_yield`` is the human string ("4 servings",
    # "1 loaf"); ``servings`` is the numeric count used for scaling + planning.
    recipe_yield: Mapped[str] = mapped_column(String(120), default="")
    servings: Mapped[int] = mapped_column(Integer, default=0)

    # Times in whole minutes (0 = unknown). Kept numeric for planning/sorting.
    prep_minutes: Mapped[int] = mapped_column(Integer, default=0)
    cook_minutes: Mapped[int] = mapped_column(Integer, default=0)
    total_minutes: Mapped[int] = mapped_column(Integer, default=0)

    source_url: Mapped[str] = mapped_column(String(1024), default="")
    # Image file name stored under DATA_DIR/images; served via /api/v1/recipes/<id>/image.
    image: Mapped[str] = mapped_column(String(255), default="")

    rating: Mapped[int] = mapped_column(Integer, default=0)  # 0-5
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Nutrition kept as a JSON blob until there's a third consumer worth
    # normalizing for (calories, protein, fat, carbs, …).
    nutrition: Mapped[str] = mapped_column(Text, default="")

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="recipes")

    ingredients = relationship(
        "RecipeIngredient",
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeIngredient.position",
    )
    steps = relationship(
        "RecipeStep",
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeStep.position",
    )

    categories = relationship(
        "Category", secondary="recipe_categories", back_populates="recipes"
    )
    tags = relationship("Tag", secondary="recipe_tags", back_populates="recipes")


class RecipeIngredient(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "recipe_ingredients"

    # ``display`` is the original free-text line ("2 cloves garlic, minced").
    # quantity/unit/food are the parsed structured form (any may be empty when
    # parsing was partial) — used for scaling, shopping lists, and pantry match.
    display: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(String(512), default="")
    # Optional section heading this line falls under ("For the sauce").
    section: Mapped[str] = mapped_column(String(255), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)

    recipe_id: Mapped[str] = mapped_column(String(36), ForeignKey("recipes.id"))
    recipe = relationship("Recipe", back_populates="ingredients")

    unit_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("units.id"), nullable=True
    )
    unit = relationship("Unit")

    food_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("foods.id"), nullable=True
    )
    food = relationship("Food")


class RecipeStep(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "recipe_steps"

    position: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(String(255), default="")

    recipe_id: Mapped[str] = mapped_column(String(36), ForeignKey("recipes.id"))
    recipe = relationship("Recipe", back_populates="steps")
