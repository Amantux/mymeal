"""AI tooling: confidence-gated auto-tag recipes, cluster proposals, review queue,
and ICL feedback. No live vendor calls (a fake provider is injected)."""
from app.extensions import db
from app.models import AiSuggestion, Group, Recipe, Tag
from app.services import jobs


def _recipe(gid, name="Omelette"):
    r = Recipe(name=name, slug=name.lower().replace(" ", "-"), group_id=gid, servings=2)
    db.session.add(r)
    db.session.commit()
    return r


class _FakeProvider:
    def __init__(self, response):
        self.response = response

    def complete_json(self, prompt, system="", max_tokens=4096):
        return self.response


def _use_provider(monkeypatch, provider):
    monkeypatch.setattr("app.services.ai.registry.provider_for_group",
                        lambda gid, settings=None, model=None: provider)


def _run(kind, gid):
    jobs.enqueue(kind, gid)
    jobs.run_job(jobs.claim_one())


def test_categorize_auto_applies_high_confidence_existing_tag(auth_client, app, monkeypatch, gid):

    with app.app_context():
        db.session.add(Tag(name="Breakfast", slug="breakfast", group_id=gid))
        _recipe(gid, "Omelette")
        db.session.commit()
        _use_provider(monkeypatch, _FakeProvider(
            {"label": "Breakfast", "confidence": 0.95, "rationale": "eggs"}))

        _run("categorize", gid)

        r = db.session.query(Recipe).filter_by(name="Omelette").first()
        assert "Breakfast" in [t.name for t in r.tags]
        assert db.session.query(AiSuggestion).filter_by(status="accepted").count() == 1


def test_categorize_queues_new_tag(auth_client, app, monkeypatch, gid):

    with app.app_context():
        _recipe(gid, "Mystery dish")
        _use_provider(monkeypatch, _FakeProvider(
            {"label": "Fusion", "confidence": 0.99}))  # new tag → review, never auto-create

        _run("categorize", gid)

        r = db.session.query(Recipe).filter_by(name="Mystery dish").first()
        assert r.tags == []
        assert db.session.query(Tag).count() == 0
        assert db.session.query(AiSuggestion).filter_by(status="pending").count() == 1


def test_categorize_rerun_does_not_duplicate_pending(auth_client, app, monkeypatch, gid):

    with app.app_context():
        _recipe(gid, "Mystery dish")
        _use_provider(monkeypatch, _FakeProvider({"label": "Fusion", "confidence": 0.4}))
        _run("categorize", gid)
        _run("categorize", gid)
        assert db.session.query(AiSuggestion).filter_by(status="pending").count() == 1


def test_categorize_malformed_confidence_completes(auth_client, app, monkeypatch, gid):

    with app.app_context():
        _recipe(gid, "Thing")
        _use_provider(monkeypatch, _FakeProvider({"label": "X", "confidence": "high"}))
        _run("categorize", gid)
        from app.models import Job
        assert db.session.query(Job).filter_by(kind="categorize").first().status == "done"


def test_accept_categorize_creates_and_applies_tag(auth_client, app, gid):

    with app.app_context():
        r = _recipe(gid, "Pancakes")
        s = AiSuggestion(kind="categorize", status="pending", label="Breakfast",
                         confidence=0.4, recipe_id=r.id, group_id=gid)
        db.session.add(s)
        db.session.commit()
        sid, rid = s.id, r.id
    assert auth_client.post(f"/api/v1/suggestions/{sid}/accept").status_code == 200
    with app.app_context():
        r = db.session.get(Recipe, rid)
        assert "Breakfast" in [t.name for t in r.tags]  # tag created (with slug) + attached


def test_cluster_and_accept_tags_members(auth_client, app, monkeypatch, gid):

    with app.app_context():
        a, b, c = _recipe(gid, "Tacos"), _recipe(gid, "Burrito"), _recipe(gid, "Cake")
        _use_provider(monkeypatch, _FakeProvider(
            {"clusters": [{"name": "Mexican", "indices": [0, 1], "confidence": 0.9}]}))
        _run("cluster", gid)
        s = db.session.query(AiSuggestion).filter_by(kind="cluster", status="pending").first()
        assert s and s.label == "Mexican"
        sid, aid, bid, cid = s.id, a.id, b.id, c.id
    assert auth_client.post(f"/api/v1/suggestions/{sid}/accept").status_code == 200
    with app.app_context():
        for rid in (aid, bid):
            assert "Mexican" in [t.name for t in db.session.get(Recipe, rid).tags]
        assert db.session.get(Recipe, cid).tags == []  # not a member


def test_reject_suggestion(auth_client, app, gid):

    with app.app_context():
        s = AiSuggestion(kind="categorize", status="pending", label="Nope",
                         confidence=0.3, group_id=gid)
        db.session.add(s)
        db.session.commit()
        sid = s.id
    assert auth_client.post(f"/api/v1/suggestions/{sid}/reject").status_code == 200
    with app.app_context():
        assert db.session.get(AiSuggestion, sid).status == "rejected"


def test_delete_recipe_with_suggestion_succeeds(auth_client, app, gid):

    with app.app_context():
        r = _recipe(gid, "Disposable")
        db.session.add(AiSuggestion(kind="categorize", status="accepted", label="X",
                                    confidence=0.9, recipe_id=r.id, group_id=gid))
        db.session.commit()
        rid = r.id
    assert auth_client.delete(f"/api/v1/recipes/{rid}").status_code in (200, 204)
    with app.app_context():
        assert db.session.query(AiSuggestion).filter_by(recipe_id=rid).count() == 0


def test_categorize_applies_model_override(auth_client, app, monkeypatch, gid):

    captured = {}
    with app.app_context():
        _recipe(gid, "X")

        def fake_pfg(gid, settings=None, model=None):
            captured["model"] = model
            return _FakeProvider({"label": "T", "confidence": 0.1})
        monkeypatch.setattr("app.services.ai.registry.provider_for_group", fake_pfg)
        jobs.enqueue("categorize", gid, {"model": "llama3.2"})
        jobs.run_job(jobs.claim_one())
        assert captured["model"] == "llama3.2"


def test_suggestion_cross_group_404(auth_client, app):
    with app.app_context():
        other = Group(name="Other")
        db.session.add(other)
        db.session.flush()
        s = AiSuggestion(kind="categorize", status="pending", label="x", group_id=other.id)
        db.session.add(s)
        db.session.commit()
        sid = s.id
    assert auth_client.post(f"/api/v1/suggestions/{sid}/accept").status_code == 404
