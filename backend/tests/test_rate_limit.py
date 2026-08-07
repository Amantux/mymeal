"""Rate limiting is wired and bounds the sensitive endpoints.

myMeal shipped with NO rate limiting while both sibling apps limited login/
chat/import (sibling divergence). This asserts the limiter is active when
RATELIMIT_ENABLED is on (tests disable it globally, so this test builds its own
app with it ON) and that it does NOT leak past the limit.
"""
import tempfile

import pytest

from app import create_app
from app.config import Config


@pytest.fixture()
def limited_app():
    d = tempfile.mkdtemp()

    class C(Config):
        DATA_DIR = d
        DATABASE_URL = f"sqlite:///{d}/rl.db"
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"
        DISABLE_AUTH = False
        RATELIMIT_ENABLED = True   # override the test default
        WORKER_ENABLED = False

    return create_app(C)


def test_login_is_rate_limited(limited_app):
    c = limited_app.test_client()
    body = {"username": "nobody@t.com", "password": "x"}
    codes = [c.post("/api/v1/users/login", json=body).status_code
             for _ in range(8)]
    # 5/min → the first 5 are 401 (bad creds), then 429s appear.
    assert 429 in codes, f"login was not rate limited: {codes}"
    assert codes.count(401) <= 5


def test_a_normal_request_is_not_limited(limited_app):
    c = limited_app.test_client()
    # the health/status endpoint carries no limit
    assert all(c.get("/api/v1/misc/health").status_code == 200
               for _ in range(20))
