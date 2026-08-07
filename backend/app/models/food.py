"""Canonical foods (ingredients) and units of measure.

A ``Food`` is the normalized thing you buy or keep in the pantry (e.g. "olive
oil"), independent of any recipe. Recipe ingredients, pantry items, and
shopping-list items all point at a Food so quantities can be consolidated and
matched. ``aisle`` groups foods for tidy shopping lists.
"""
from sqlalchemy import JSON, String, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class Food(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "foods"

    name: Mapped[str] = mapped_column(String(255), index=True)
    plural_name: Mapped[str] = mapped_column(String(255), default="")
    # Alternate names, used for matching on import. A JSON list (migration
    # 0014); writes go through services.food_resolve.set_aliases, which owns
    # the one-alias-one-food invariant. server_default so create_all() and a
    # migrated database describe the same table.
    aliases: Mapped[list] = mapped_column(JSON, default=list,
                                          server_default=text("'[]'"))
    # Supermarket aisle / department for grouping shopping lists.
    aisle: Mapped[str] = mapped_column(String(120), default="")
    # What KIND of thing this is ("dairy", "grain") and what it contains
    # ("dairy", "nuts"). These are what stop a substring match crossing a
    # material boundary — "almond milk" must never resolve to `milk`. Same
    # fields as Edibl's FoodConcept; see docs/adr/0001.
    classification: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # server_default so create_all() and a 0015-migrated database agree —
    # this column is the one that broke 0013's own stated rule and produced
    # two schemas from one codebase.
    allergens: Mapped[list] = mapped_column(JSON, default=list,
                                            server_default=text("'[]'"))
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
