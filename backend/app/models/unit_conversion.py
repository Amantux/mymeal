"""Learned unit→weight conversions.

The built-in density table in ``services/units`` covers common pantry foods by
volume. It cannot cover count units: there is no honest gram value for "a
clove", and inventing one would make every weight readout quietly wrong. But
"1 stick of butter" does have a real, well-known answer (113 g), and the app can
look it up once and remember it.

A row is one answer to "how many grams is <unit> of <food_term>". ``source``
records where it came from, because a number the app found on the internet must
never be indistinguishable from one it ships with.
"""
from typing import Optional

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .base import IDMixin, TimestampMixin


class UnitConversion(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "unit_conversions"
    __table_args__ = (
        # One answer per unit+food per household. The lookup upserts on this, so
        # a second recipe using sticks of butter costs nothing.
        UniqueConstraint("group_id", "unit", "food_term",
                         name="uq_unit_conversion_scope"),
    )

    unit: Mapped[str] = mapped_column(String(40), index=True)
    # A normalised keyword, not the whole ingredient line: "butter", not
    # "2 sticks unsalted butter, softened".
    food_term: Mapped[str] = mapped_column(String(120), index=True)
    grams_per_unit: Mapped[float] = mapped_column(Float)

    # builtin | web | user — shown to the user, never guessed.
    source: Mapped[str] = mapped_column(String(16), default="web")
    source_url: Mapped[str] = mapped_column(String(1024), default="", server_default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # confirmed | pending. A pending row is NEVER used in a calculation; it sits
    # in the review list until a human accepts it.
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    group_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("groups.id"), index=True, nullable=True
    )
