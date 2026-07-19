"""Status / health endpoints."""
from flask import Blueprint, jsonify

bp = Blueprint("misc", __name__)


@bp.get("/status")
def status():
    return jsonify(
        {
            "health": True,
            "versions": ["v1"],
            "title": "myMeal",
            "message": "myMeal — recipes, planning & AI cooking assistant",
        }
    )


@bp.get("/misc/health")
def health():
    return jsonify({"ok": True})
