"""Background job engine (ported from HomeHoard): enqueue, atomic claim, run, reap,
and the bulk-nutrition handler.

Worker poller disabled in tests (WORKER_ENABLED=False); these drive the job
functions directly. No live vendor calls (estimate_nutrition + provider stubbed).
"""
import json
from datetime import timedelta

from app.extensions import db
from app.models import Group, Job, Recipe, RecipeIngredient, User, utcnow
from app.services import jobs


def _gid(app):
    with app.app_context():
        return db.session.query(User).filter_by(email="t@t.com").first().group_id


def _recipe(gid, name="Omelette", servings=2, nutrition="", ings=("3 eggs",)):
    r = Recipe(name=name, slug=name.lower().replace(" ", "-"), group_id=gid,
               servings=servings, nutrition=nutrition)
    db.session.add(r)
    db.session.flush()
    for line in ings:
        db.session.add(RecipeIngredient(display=line, recipe_id=r.id))
    db.session.commit()
    return r


def test_enqueue_creates_pending_and_dedups(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        a = jobs.enqueue("nutrition", gid)
        b = jobs.enqueue("nutrition", gid)
        assert a.id == b.id and a.status == "pending"


def test_claim_is_atomic_no_double_run(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        jobs.enqueue("nutrition", gid)
        assert jobs.claim_one() is not None
        assert jobs.claim_one() is None


def test_nutrition_job_estimates_missing(auth_client, app, monkeypatch):
    gid = _gid(app)
    with app.app_context():
        _recipe(gid, name="Omelette")                       # missing → estimated
        _recipe(gid, name="Toast", nutrition='{"calories":90}')  # already has it
        monkeypatch.setattr("app.services.ai.registry.provider_for_group",
                            lambda gid, settings=None: object())
        monkeypatch.setattr("app.services.ai.nutrition.estimate_nutrition",
                            lambda lines, servings, provider: {"calories": 210})

        job = jobs.enqueue("nutrition", gid)
        jobs.run_job(jobs.claim_one())

        done = db.session.get(Job, job.id)
        assert done.status == "done"
        result = json.loads(done.result)
        assert result["estimated"] == 1 and result["remaining"] == 0
        om = db.session.query(Recipe).filter_by(name="Omelette").first()
        assert json.loads(om.nutrition)["calories"] == 210


def test_nutrition_job_errors_when_no_provider(auth_client, app, monkeypatch):
    from app.services.ai.base import ProviderError
    gid = _gid(app)
    with app.app_context():
        _recipe(gid)

        def _raise(gid, settings=None):
            raise ProviderError("no provider")
        monkeypatch.setattr("app.services.ai.registry.provider_for_group", _raise)
        job = jobs.enqueue("nutrition", gid)
        jobs.run_job(jobs.claim_one())
        assert db.session.get(Job, job.id).status == "error"


def test_reap_stale_requeues_dead_and_spares_live(auth_client, app):
    gid = _gid(app)
    with app.app_context():
        dead = Job(kind="nutrition", group_id=gid, status="running",
                   started_at=utcnow() - timedelta(hours=1))
        db.session.add(dead)
        db.session.commit()
        assert jobs.reap_stale() == 1
        assert db.session.get(Job, dead.id).status == "pending"
        live = Job(kind="cleanup", group_id=gid, status="running", started_at=utcnow())
        db.session.add(live)
        db.session.commit()
        assert jobs.reap_stale() == 0
        assert db.session.get(Job, live.id).status == "running"


def test_only_one_active_job_per_group_kind_enforced(auth_client, app):
    from sqlalchemy.exc import IntegrityError
    gid = _gid(app)
    with app.app_context():
        db.session.add(Job(kind="nutrition", group_id=gid, status="pending"))
        db.session.commit()
        db.session.add(Job(kind="nutrition", group_id=gid, status="running"))
        try:
            db.session.commit()
            assert False, "expected the partial-unique constraint to fire"
        except IntegrityError:
            db.session.rollback()


def test_create_job_endpoint_and_poll(auth_client):
    r = auth_client.post("/api/v1/jobs/nutrition")
    assert r.status_code == 202
    jid = r.get_json()["id"]
    assert auth_client.get(f"/api/v1/jobs/{jid}").get_json()["status"] == "pending"


def test_create_job_unknown_kind_404(auth_client):
    assert auth_client.post("/api/v1/jobs/bogus").status_code == 404


def test_job_get_cross_group_404(auth_client, app):
    with app.app_context():
        other = Group(name="Other")
        db.session.add(other)
        db.session.flush()
        j = Job(kind="nutrition", group_id=other.id)
        db.session.add(j)
        db.session.commit()
        jid = j.id
    assert auth_client.get(f"/api/v1/jobs/{jid}").status_code == 404
