"""Public, UNAUTHENTICATED read-only access to a shared recipe.

Reachable only by knowing a recipe's opaque ``share_token`` (256-bit,
unguessable). Returns a deliberately minimal view (display fields only — no ids,
no group, no owner) and serves the recipe image by the same token. Revoking the
token (DELETE /recipes/<id>/share) makes every route here 404 immediately.
"""
import os

from flask import Blueprint, abort, jsonify, send_file

from ..extensions import db
from ..models import Recipe
from ..schemas.serializers import recipe_public_out
from .recipes import _image_path

bp = Blueprint("public", __name__)


def _by_token(token: str) -> Recipe:
    # Guard the empty/None case explicitly: unshared recipes have share_token
    # NULL, and an empty token must never match them.
    if not token:
        abort(404)
    recipe = db.session.query(Recipe).filter_by(share_token=token).first()
    if not recipe:
        abort(404)
    return recipe


@bp.get("/public/recipes/<token>")
def public_recipe(token):
    return jsonify(recipe_public_out(_by_token(token)))


@bp.get("/public/recipes/<token>/image")
def public_recipe_image(token):
    recipe = _by_token(token)
    if not recipe.image:
        abort(404)
    path = _image_path(recipe.image)
    if not os.path.isfile(path):
        abort(404)
    return send_file(path)
