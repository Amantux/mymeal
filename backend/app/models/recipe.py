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

    # Oven temperature in Celsius, unrounded (350°F is 176.67°C — storing 177
    # and converting back would show 351°F and stop matching the recipe).
    # Nullable: "no temperature" is a real answer for a salad, and distinct
    # from 0.
    cook_temperature_c: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )

    source_url: Mapped[str] = mapped_column(String(1024), default="")

    # How-to videos. delete-orphan: removing a recipe takes its videos with it.
    videos = relationship(
        "RecipeVideo", back_populates="recipe", cascade="all, delete-orphan",
        order_by="RecipeVideo.position",
    )
    # Image file name stored under DATA_DIR/images; served via /api/v1/recipes/<id>/image.
    image: Mapped[str] = mapped_column(String(255), default="")

    rating: Mapped[int] = mapped_column(Integer, default=0)  # 0-5
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Nutrition kept as a JSON blob until there's a third consumer worth
    # normalizing for (calories, protein, fat, carbs, …).
    nutrition: Mapped[str] = mapped_column(Text, default="")

    # Opaque token for a public, read-only share link. None = not shared.
    share_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True, default=None
    )

    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id"), index=True)
    group = relationship("Group", back_populates="recipes")

    ingredients = relationship(
        "RecipeIngredient",
        back_populates="recipe",
        foreign_keys="RecipeIngredient.recipe_id",
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

    # Version history + experiments (auto snapshots on save + deliberate branches).
    versions = relationship(
        "RecipeVersion",
        back_populates="recipe",
        foreign_keys="RecipeVersion.recipe_id",
        cascade="all, delete-orphan",
        order_by="RecipeVersion.created_at.desc()",
    )


class RecipeIngredient(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "recipe_ingredients"

    # ``display`` is the original free-text line ("2 cloves garlic, minced").
    # quantity/unit/food are the parsed structured form (any may be empty when
    # parsing was partial) — used for scaling, shopping lists, and pantry match.
    display: Mapped[str] = mapped_column(Text, default="")
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(String(512), default="")
    # The VARIETY, split off the food by services.food_resolve: "Vietnamese" for
    # "Vietnamese cinnamon". Deliberately NOT `note` — note means preparation
    # ("finely chopped"), and merging the two makes them indistinguishable
    # forever. `display` still holds the human's original wording.
    qualifier: Mapped[str] = mapped_column(String(120), default="", server_default="")
    # Optional section heading this line falls under ("For the sauce").
    section: Mapped[str] = mapped_column(String(255), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)

    recipe_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recipes.id"), index=True)
    recipe = relationship("Recipe", back_populates="ingredients", foreign_keys=[recipe_id])

    # Optional link to ANOTHER recipe used as a component ("1 batch Garlic
    # Confit"). SET NULL so the referenced recipe can still be deleted (the row
    # then falls back to its plain display text).
    ref_recipe_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    ref_recipe = relationship("Recipe", foreign_keys=[ref_recipe_id])

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

    recipe_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recipes.id"), index=True)
    recipe = relationship("Recipe", back_populates="steps")


class RecipeVersion(IDMixin, TimestampMixin, db.Model):
    """A point-in-time snapshot of a recipe's content (JSON in the `_apply`
    payload shape). `kind="auto"` rows form the edit-history timeline (captured
    before each save, pruned to a cap); `kind="experiment"` rows are deliberate
    branches the cook edits, cooks, rates, and either promotes into the live
    recipe or discards. Group-scoped like everything else."""

    __tablename__ = "recipe_versions"

    recipe_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recipes.id", ondelete="CASCADE"), index=True
    )
    recipe = relationship("Recipe", back_populates="versions", foreign_keys=[recipe_id])

    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), default="auto")  # auto | experiment
    # experiment lifecycle: open | promoted | discarded (auto rows stay "open").
    status: Mapped[str] = mapped_column(String(16), default="open")
    label: Mapped[str] = mapped_column(String(255), default="")
    note: Mapped[str] = mapped_column(Text, default="")  # the experiment idea
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # feedback 0-5
    feedback: Mapped[str] = mapped_column(Text, default="")  # how it turned out
    parent_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    snapshot: Mapped[str] = mapped_column(Text, default="")  # JSON, _apply-shaped
    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
