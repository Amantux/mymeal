from datetime import date

from sqlalchemy import String, Float, Date, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class PantryItem(IDMixin, TimestampMixin, db.Model):
    """Something currently in stock at home.

    Points at a canonical ``Food`` when one is known (so recipes can be matched
    against the pantry), and always carries a display ``label`` for the case
    where no Food row exists yet.
    """

    __tablename__ = "pantry_items"

    label: Mapped[str] = mapped_column(String(255), index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(64), default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    expires_at: Mapped[date] = mapped_column(Date, nullable=True)

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="pantry_items")

    food_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("foods.id"), nullable=True
    )
    food = relationship("Food")
