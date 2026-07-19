from sqlalchemy import String, Float, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import IDMixin, TimestampMixin


class ShoppingList(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "shopping_lists"

    name: Mapped[str] = mapped_column(String(255), default="Shopping List")

    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"))
    group = relationship("Group", back_populates="shopping_lists")

    items = relationship(
        "ShoppingListItem",
        back_populates="shopping_list",
        cascade="all, delete-orphan",
        order_by="ShoppingListItem.position",
    )


class ShoppingListItem(IDMixin, TimestampMixin, db.Model):
    __tablename__ = "shopping_list_items"

    display: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    unit: Mapped[str] = mapped_column(String(64), default="")
    # Supermarket aisle/department, used to group the list for shopping.
    aisle: Mapped[str] = mapped_column(String(120), default="")
    checked: Mapped[bool] = mapped_column(Boolean, default=False)
    position: Mapped[int] = mapped_column(Integer, default=0)

    shopping_list_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("shopping_lists.id")
    )
    shopping_list = relationship("ShoppingList", back_populates="items")

    food_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("foods.id"), nullable=True
    )
    food = relationship("Food")
