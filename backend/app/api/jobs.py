"""Background-job endpoints: enqueue an async task, poll its progress.

Jobs are group-scoped. Enqueuing bulk AI tooling is owner-only (external, paid
calls); reading progress is any group member. The worker (worker.py) runs them.
"""
import json

from flask import Blueprint, jsonify, request, abort

from ..extensions import db
from ..models import Job
from ..auth import login_required, owner_required, current_group
from ..services.jobs import enqueue, known_kinds, JobError

bp = Blueprint("jobs", __name__)


def _job_out(j: Job) -> dict:
    return {
        "id": j.id, "kind": j.kind, "status": j.status,
        "total": j.total, "done": j.done,
        "result": json.loads(j.result) if j.result else None,
        "error": j.error or None,
        "params": json.loads(j.params) if j.params else {},
        "createdAt": j.created_at.isoformat() if j.created_at else None,
        "updatedAt": j.updated_at.isoformat() if j.updated_at else None,
    }


def _get_job(job_id) -> Job:
    j = db.session.get(Job, job_id)
    if not j or j.group_id != current_group().id:
        abort(404)
    return j


@bp.post("/jobs/<kind>")
@owner_required
def create_job(kind):
    """Enqueue (or resume) a background job of this kind for the group. Optional JSON
    body: ``note`` (extra AI guidance) and ``model`` (override the provider's model)."""
    if kind not in known_kinds():
        return jsonify({"error": f"unknown job kind '{kind}'"}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = {}
    params = {}
    if data.get("note"):
        params["note"] = str(data["note"])[:1000]
    if data.get("model"):
        params["model"] = str(data["model"])[:100]
    try:
        job = enqueue(kind, current_group().id, params or None)
    except JobError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_job_out(job)), 202


@bp.get("/jobs/<job_id>")
@login_required
def get_job(job_id):
    return jsonify(_job_out(_get_job(job_id)))


@bp.get("/jobs")
@login_required
def list_jobs():
    q = db.session.query(Job).filter(Job.group_id == current_group().id)
    kind = request.args.get("kind")
    if kind:
        q = q.filter(Job.kind == kind)
    jobs = q.order_by(Job.created_at.desc()).limit(20).all()
    return jsonify({"items": [_job_out(j) for j in jobs]})
