"""Canonical foods (ingredients) and units of measure.

A ``Food`` is the normalized thing you buy or keep in the pantry (e.g. "olive
oil"), independent of any recipe. Recipe ingredients, pantry items, and
shopping-list items all point at a Food so quantities can be consolidated and
matched. ``aisle`` groups foods for tidy shopping lists.
"""
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class Food(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "foods"

    name: Mapped[str] = mapped_column(String(255), index=True)
    plural_name: Mapped[str] = mapped_column(String(255), default="")
    # Comma-separated alternate names, used for fuzzy matching on import.
    aliases: Mapped[str] = mapped_column(String(512), default="")
    # Supermarket aisle / department for grouping shopping lists.
    aisle: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(String(512), default="")

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="foods")


class Unit(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "units"

    name: Mapped[str] = mapped_column(String(120), index=True)
    plural_name: Mapped[str] = mapped_column(String(120), default="")
    abbreviation: Mapped[str] = mapped_column(String(32), default="")

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="units")
