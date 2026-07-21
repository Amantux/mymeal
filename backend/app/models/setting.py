"""Per-household runtime settings, persisted so they survive restarts.

A small key/value store scoped to a group. Today it backs the AI-provider
config the user sets in the UI: values here OVERRIDE the env / add-on defaults,
so a provider can be configured in Home Assistant *or* in the myMeal UI and is
remembered either way (mirrors how the companion Edibl app does it).

Only AI-provider keys use this table right now; the rest of the configuration
contract (ports, auth, storage) stays env/add-on-only.
"""
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class Setting(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("group_id", "key", name="uq_setting_group_key"),)

    key: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[str] = mapped_column(Text, default="")

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), index=True)
    group = relationship("Group")
