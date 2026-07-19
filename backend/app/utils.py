"""Small shared helpers."""
from slugify import slugify as _slugify

from .extensions import db


def slugify(text: str) -> str:
    return _slugify(text or "") or "untitled"


def unique_slug(model, group_id: str, name: str, exclude_id: str | None = None) -> str:
    """A slug for ``name`` that is unique within ``group_id`` for ``model``.

    Appends ``-2``, ``-3``, … on collision. ``exclude_id`` lets an update keep
    its own slug without colliding with itself.
    """
    base = slugify(name)
    slug = base
    n = 1
    while True:
        q = db.session.query(model).filter_by(group_id=group_id, slug=slug)
        if exclude_id:
            q = q.filter(model.id != exclude_id)
        if not q.first():
            return slug
        n += 1
        slug = f"{base}-{n}"
