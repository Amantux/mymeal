from datetime import date

from sqlalchemy import String, Integer, Date, Text, ForeignKey, Index
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

    # Every read of this table is "one household, a date range" — the plan
    # views, the shopping-list builder, and now the public calendar feed, which
    # is unauthenticated and polled on a timer. Migration 0016 indexed the hot
    # tenant/FK columns but missed this table; a composite (group_id, date)
    # serves the compound filter better than either column alone, and Postgres
    # does not index a foreign key for you.
    __table_args__ = (
        Index("ix_mealplan_entries_group_id_date", "group_id", "date"),
    )

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
