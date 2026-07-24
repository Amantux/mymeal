from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .base import IDMixin, TimestampMixin


class Medication(IDMixin, TimestampMixin, db.Model):
    """A medication, vitamin, or supplement a household member takes.

    Personal to a user (and scoped to their household group): only the owning
    user sees or edits their own entries. Dose is structured (amount + unit);
    frequency covers the common cases (daily N×, weekly on given days, as-needed)
    plus optional clock times for reminders.
    """

    __tablename__ = "medications"

    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32), default="medication")  # medication|vitamin|supplement
    dose_amount: Mapped[float] = mapped_column(Float, default=0.0)
    dose_unit: Mapped[str] = mapped_column(String(64), default="")  # mg, IU, mL, tablet, capsule…
    frequency: Mapped[str] = mapped_column(String(32), default="daily")  # daily|weekly|as_needed
    times_per_day: Mapped[int] = mapped_column(Integer, default=1)
    schedule_times: Mapped[str] = mapped_column(String(255), default="")  # "08:00,20:00"
    days_of_week: Mapped[str] = mapped_column(String(64), default="")  # weekly: "mon,wed,fri"
    with_food: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(String(1024), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), index=True)
