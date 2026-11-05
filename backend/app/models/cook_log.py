"""Cook event tracking: when a recipe was cooked and how many times."""
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class CookEvent(IDMixin, TimestampMixin, db.Model):
    """A record that a recipe was cooked.

    Each cook event is tied to a specific recipe and group (tenant).
    When a recipe is deleted, all its cook events are removed (cascade).
    """
    __tablename__ = "cook_events"

    recipe_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recipes.id", ondelete="CASCADE"), index=True
    )
    recipe = relationship("Recipe", back_populates="cook_events")

    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id"), index=True
    )
    group = relationship("Group")
