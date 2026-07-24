"""First-boot household bootstrap must not mint two groups under concurrency.

The old code read "no group" then created one, so two *different* users hitting
a fresh install at once each made a household. _get_or_create_household now
serializes that behind a lock (fcntl on SQLite, advisory on Postgres).
"""
import threading

from app import create_app
from app.config import Config
from app.extensions import db
from app.models import Group


def _cfg(tmp_path):
    class C(Config):
        DATA_DIR = str(tmp_path)
        DATABASE_URL = f"sqlite:///{tmp_path}/race.db"  # file DB shared across threads
        SECRET_KEY = "test-secret-key-that-is-long-enough-32b"
        DISABLE_AUTH = True

    return C


def test_concurrent_bootstrap_creates_exactly_one_household(tmp_path):
    app = create_app(_cfg(tmp_path))
    from app.auth import _get_or_create_household

    with app.app_context():
        assert db.session.query(Group).count() == 0  # fresh install

    n = 8
    start = threading.Barrier(n)
    errors = []

    def worker():
        with app.app_context():
            start.wait()  # maximise contention
            try:
                _get_or_create_household()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"bootstrap raised under contention: {errors}"
    with app.app_context():
        assert db.session.query(Group).count() == 1  # exactly one household
