"""Per-user medications / vitamins / supplements with dose + frequency.

Health data is PERSONAL: every query is scoped to the current user, and a
member can only read or change their own entries (no cross-user access, even
within the same household).
"""
from flask import Blueprint, request, jsonify, abort

from ..extensions import db
from ..models import Medication
from ..auth import login_required, current_user, current_group
from ..schemas.serializers import medication_out

bp = Blueprint("medications", __name__)

_KINDS = {"medication", "vitamin", "supplement"}
_FREQS = {"daily", "weekly", "as_needed"}


def _get(med_id):
    m = db.session.get(Medication, med_id)
    if not m or m.user_id != current_user().id:
        abort(404)  # own-only — never reveal another member's health entries
    return m


def _apply(m: Medication, data: dict):
    if "name" in data:
        m.name = str(data.get("name") or "").strip()[:255]
    if "kind" in data:
        k = str(data.get("kind") or "").strip().lower()
        m.kind = k if k in _KINDS else "medication"
    if "doseAmount" in data:
        try:
            m.dose_amount = max(0.0, float(data.get("doseAmount") or 0))
        except (TypeError, ValueError):
            m.dose_amount = 0.0
    if "doseUnit" in data:
        m.dose_unit = str(data.get("doseUnit") or "").strip()[:64]
    if "frequency" in data:
        f = str(data.get("frequency") or "").strip().lower()
        m.frequency = f if f in _FREQS else "daily"
    if "timesPerDay" in data:
        try:
            m.times_per_day = min(24, max(1, int(data.get("timesPerDay") or 1)))
        except (TypeError, ValueError):
            m.times_per_day = 1
    if "scheduleTimes" in data:
        m.schedule_times = str(data.get("scheduleTimes") or "").strip()[:255]
    if "daysOfWeek" in data:
        m.days_of_week = str(data.get("daysOfWeek") or "").strip()[:64]
    if "withFood" in data:
        m.with_food = bool(data.get("withFood"))
    if "notes" in data:
        m.notes = str(data.get("notes") or "").strip()[:1024]
    if "active" in data:
        m.active = bool(data.get("active"))


@bp.get("/medications")
@login_required
def list_medications():
    meds = (
        db.session.query(Medication)
        .filter_by(user_id=current_user().id)
        .order_by(Medication.active.desc(), Medication.name.asc())
        .all()
    )
    return jsonify({"items": [medication_out(m) for m in meds]})


@bp.post("/medications")
@login_required
def create_medication():
    data = request.get_json(silent=True) or {}
    if not str(data.get("name") or "").strip():
        return jsonify({"error": "a name is required"}), 422
    m = Medication(user_id=current_user().id, group_id=current_group().id)
    _apply(m, data)
    db.session.add(m)
    db.session.commit()
    return jsonify(medication_out(m)), 201


@bp.put("/medications/<med_id>")
@login_required
def update_medication(med_id):
    m = _get(med_id)
    _apply(m, request.get_json(silent=True) or {})
    if not m.name:
        return jsonify({"error": "a name is required"}), 422
    db.session.commit()
    return jsonify(medication_out(m))


@bp.delete("/medications/<med_id>")
@login_required
def delete_medication(med_id):
    db.session.delete(_get(med_id))
    db.session.commit()
    return "", 204
