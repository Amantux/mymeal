from datetime import date

from sqlalchemy import String, Integer, Date, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class MealPlanEntry(IDMixin, TimestampMixin, db.Model):
    """A single planned meal on a date.

    A weekly plan is just the set of entries whose ``date`` falls in that week
    — there is no separate plan container. An entry references a recipe, or
    carries free text (``title``) for a meal that isn't a saved recipe.
    """

    __tablename__ = "mealplan_entries"

    date: Mapped[date] = mapped_column(Date, index=True)
    # breakfast | lunch | dinner | snack | side
    meal_type: Mapped[str] = mapped_column(String(32), default="dinner")
    title: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    servings: Mapped[int] = mapped_column(Integer, default=0)

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="mealplan_entries")

    recipe_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recipes.id"), nullable=True
    )
    recipe = relationship("Recipe")
