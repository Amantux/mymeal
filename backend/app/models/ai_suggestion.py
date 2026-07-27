from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class AiSuggestion(IDMixin, TimestampMixin, db.Model):
    """A proposal from an AI tooling job (categorize/cluster) awaiting review, and —
    once resolved — a training example fed back as few-shot context to later runs.

    - ``categorize``: propose a tag ``label`` for one ``recipe`` (recipe_id set).
    - ``cluster``: propose a named grouping ``label`` (a tag) over recipes listed in
      ``payload['recipeIds']`` (recipe_id null).
    """

    __tablename__ = "ai_suggestions"

    kind: Mapped[str] = mapped_column(String(16), index=True)   # categorize | cluster
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    label: Mapped[str] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[str] = mapped_column(Text, default="")       # JSON (cluster members)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    recipe_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("recipes.id", ondelete="CASCADE"), nullable=True, index=True)
    recipe = relationship("Recipe")

    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id"), index=True)
    group = relationship("Group", back_populates="ai_suggestions")
