"""Phase 3: shared-Postgres DSN resolution + find/register client guards.

The live discover→provision path needs a Supervisor + the add-on (integration
territory); here we cover the pure logic: how the persisted DSN is (or isn't)
used, and that the client no-ops safely when it shouldn't provision.
"""
from app import pg_provision
from app.settings import load_settings


def _settings(tmp_path, **over):
    over["DATA_DIR"] = str(tmp_path)
    over.setdefault("SECRET_KEY", "x" * 40)
    return load_settings(env={}, overrides=over, ha_options={}, strict_secret=False)


def test_sqlalchemy_uri_defaults_to_sqlite(tmp_path):
    assert _settings(tmp_path).sqlalchemy_uri.startswith("sqlite:///")


def test_explicit_database_url_wins_over_shared(tmp_path):
    (tmp_path / ".database_url").write_text("postgresql+psycopg://shared/mymeal")
    s = _settings(tmp_path, DATABASE_URL="postgresql+psycopg://explicit/db",
                  USE_SHARED_POSTGRES=True)
    assert s.sqlalchemy_uri == "postgresql+psycopg://explicit/db"


def test_shared_postgres_reads_persisted_dsn(tmp_path):
    (tmp_path / ".database_url").write_text("postgresql+psycopg://shared/mymeal\n")
    s = _settings(tmp_path, USE_SHARED_POSTGRES=True)
    assert s.sqlalchemy_uri == "postgresql+psycopg://shared/mymeal"


def test_persisted_dsn_ignored_when_flag_off(tmp_path):
    # A stale file must not force Postgres once the user turns the flag off.
    (tmp_path / ".database_url").write_text("postgresql+psycopg://shared/mymeal")
    assert _settings(tmp_path, USE_SHARED_POSTGRES=False).sqlalchemy_uri.startswith("sqlite:///")


# --- pg_provision.main() guards: it must never provision (or write the file) ---

def _prep(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("MYMEAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MYMEAL_SECRET_KEY", "x" * 40)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_noop_when_disabled(tmp_path, monkeypatch):
    _prep(monkeypatch, tmp_path)
    assert pg_provision.main() == 0
    assert not (tmp_path / ".database_url").exists()


def test_noop_when_manual_url_set(tmp_path, monkeypatch):
    _prep(monkeypatch, tmp_path, MYMEAL_USE_SHARED_POSTGRES="true",
          MYMEAL_DATABASE_URL="postgresql+psycopg://x/y")
    assert pg_provision.main() == 0
    assert not (tmp_path / ".database_url").exists()


def test_noop_without_supervisor(tmp_path, monkeypatch):
    _prep(monkeypatch, tmp_path, MYMEAL_USE_SHARED_POSTGRES="true")
    assert pg_provision.main() == 0
    assert not (tmp_path / ".database_url").exists()
