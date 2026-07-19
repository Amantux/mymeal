from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class Group(IDMixin, TimestampMixin, db.Model):
    """A household. Every recipe, plan, and pantry item belongs to one group."""

    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(String(255))

    users = relationship("User", back_populates="group", cascade="all, delete-orphan")
    recipes = relationship(
        "Recipe", back_populates="group", cascade="all, delete-orphan"
    )
    foods = relationship("Food", back_populates="group", cascade="all, delete-orphan")
    units = relationship("Unit", back_populates="group", cascade="all, delete-orphan")
    categories = relationship(
        "Category", back_populates="group", cascade="all, delete-orphan"
    )
    tags = relationship("Tag", back_populates="group", cascade="all, delete-orphan")
    mealplan_entries = relationship(
        "MealPlanEntry", back_populates="group", cascade="all, delete-orphan"
    )
    pantry_items = relationship(
        "PantryItem", back_populates="group", cascade="all, delete-orphan"
    )
    shopping_lists = relationship(
        "ShoppingList", back_populates="group", cascade="all, delete-orphan"
    )
    chat_sessions = relationship(
        "ChatSession", back_populates="group", cascade="all, delete-orphan"
    )
    invitations = relationship(
        "GroupInvitation", back_populates="group", cascade="all, delete-orphan"
    )


class GroupInvitation(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "group_invitations"

    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[str] = mapped_column(String(64))
    uses: Mapped[int] = mapped_column(default=1)

    group_id: Mapped[str] = mapped_column(String(36), db.ForeignKey("groups.id"))
    group = relationship("Group", back_populates="invitations")
