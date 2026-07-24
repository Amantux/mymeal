# Spec — adopt the Shared PostgreSQL find/register client (Edibl & HomeHoard)

**For: the coding agent of `Amantux/edibl` and `Amantux/homehoard`.**
**Goal:** let your app use the **Shared PostgreSQL** Home Assistant add-on (it
lives in `Amantux/mymeal`, `shared_postgres/`) with a one-toggle
find-and-register flow — the same one myMeal shipped. Turn on a `use_shared_postgres`
option and the app discovers the add-on, provisions its **own** database, and
comes up on Postgres. No connection strings to type.

> **Already done — do NOT redo.** Both repos already have Alembic-managed schema
> (`_init_schema` → `alembic upgrade head`, baseline/stamp/fill-gaps) and
> `sqlalchemy_uri` supporting `sqlite` + `postgresql`. This spec is ONLY the
> client that points that Postgres support at the shared add-on.

> **Ground truth first.** Written from myMeal's shipped implementation. Confirm
> every cited file/symbol against your own tree before editing. This is **T3**
> (it decides which DB the app boots on): add failure-path tests and run a
> reviewer over the diff before declaring done.

---

## 1. The provisioning contract (what the add-on exposes)

The `shared_postgres` add-on runs Postgres + a provisioning sidecar and publishes
a Supervisor discovery message:

```
service: "shared_postgres"
config:  { host, port: 5432, provision_url: "http://<host>:8087/provision", token }
```

Provision your database (idempotent, least-privilege — you get your own DB+role,
never the superuser):

```
POST <provision_url>
Authorization: Bearer <token>
{ "app": "<your-app>" }            # "edibl" or "homehoard" — [a-z][a-z0-9_]{0,31}
→ 200 { "dsn": "postgresql+psycopg://<app>:<pw>@<host>:5432/<app>", "host", "port", "database", "user" }
```

Notes on the contract:
- **Token is always required.** Obtain it from the discovery message's
  `config.token`, or have the operator set the same token in your app's
  `postgres_provision_token` option and the add-on's `provision_token`.
- Re-provisioning an app whose password the add-on no longer has returns **409**
  (needs `?rotate=true`) — it never silently resets a live credential.
- The DSN uses the **`postgresql+psycopg://`** driver (psycopg v3) — ensure it's
  in your image (both repos already support the scheme).

## 2. What to implement (mirror myMeal)

myMeal reference files: `backend/app/pg_provision.py`,
`backend/app/settings.py` (`sqlalchemy_uri`, the two fields),
`docker-entrypoint.sh`, `backend/tests/test_pg_provision.py`.

### 2a. Two config options
- `use_shared_postgres` (bool, default false) — HA add-on option + config field.
- `postgres_provision_token` (secret, default "") — optional operator override.

Add them to your `config.py`/settings inventory with your env prefix
(`EDIBL_` / `HBOX_`) and to the add-on `config.yaml` options+schema
(`use_shared_postgres: bool`, `postgres_provision_token: "password?"`).

### 2b. Read a persisted DSN in `sqlalchemy_uri`
Your `Config.sqlalchemy_uri` already returns `DATABASE_URL` or SQLite. Insert a
middle branch: when `DATABASE_URL` is blank **and** `use_shared_postgres` is on,
read `<DATA_DIR>/.database_url` and use it if non-empty. Explicit `DATABASE_URL`
must still win; with the flag off, ignore the file (so a stale file can't force
Postgres). This avoids routing a runtime DSN through the env/options precedence
chain (a blank HA option would otherwise clobber it).

### 2c. The find/register module (`backend/app/pg_provision.py`)
Runnable as `python3 -m app.pg_provision`. Logic (all best-effort — any miss
logs and stays on SQLite; it must NEVER block startup):
1. If `DATABASE_URL` set → return (manual wins). If `use_shared_postgres` off →
   return. If `<DATA_DIR>/.database_url` already non-empty → return (stable).
2. If no `SUPERVISOR_TOKEN` → log "not under HA" and return.
3. Discover the add-on: read the discovery message (`GET /discovery`, best-effort)
   for `{host, provision_url, token}`; also build candidate `provision_url`s from
   the Supervisor `/addons` list (any slug/name containing "postgres") + fixed
   fallbacks (`http://local-shared-postgres:8087/provision`), mirroring your
   existing `discover_*` helper.
4. Token = your `postgres_provision_token` option **or** the discovery `token`.
   If none → log a clear "set postgres_provision_token" and return.
5. POST `{app:"<yourapp>"}` to each candidate with the Bearer token until one
   answers. **Validate `dsn.startswith("postgresql+psycopg://")` before writing**
   — a foreign/garbage DSN would brick every boot at `create_app`; reject + keep
   trying / stay on SQLite.
6. Write the DSN to `<DATA_DIR>/.database_url` (mode `0600`).

### 2d. Entrypoint step
In your container entrypoint, **before** gunicorn (next to your existing HA
discovery call): `python3 -m app.pg_provision || echo "...skipped"`. It writes
the file that `sqlalchemy_uri` reads; `create_app` then runs Alembic against the
right DB.

## 3. Gotchas (all cost myMeal a reviewer round — don't repeat)

- **Validate the DSN scheme before persisting** (§2c.5). Non-negotiable.
- **Never log the token or DSN** (log only the candidate URL on failure).
- **Precedence:** read the persisted DSN in `sqlalchemy_uri` directly; do NOT try
  to inject it via an env var if a blank `database_url` HA option exists (it wins
  over env and would clobber your injection).
- **Recovery:** once `.database_url` is written it's reused across restarts; if
  the shared DB is removed/rotated and the app can't boot, deleting
  `<DATA_DIR>/.database_url` re-provisions. Document this.
- **Hostile discovery is semi-in-threat-model:** HA add-ons share a trusted
  network, but a malicious add-on could publish a fake `shared_postgres`
  discovery message. The DSN-scheme check is the backstop; prefer
  `/addons`-identified hosts over blind fallbacks. Decide explicitly, don't
  ignore.

## 4. Tests (failure paths are mandatory on a T3 DB-selection change)
Adapt myMeal's `test_pg_provision.py`: `sqlalchemy_uri` precedence (explicit URL
wins; flag-off ignores a stale/blank file); `pg_provision` no-ops (disabled,
manual URL set, no Supervisor token); a **foreign-scheme DSN is rejected** (not
persisted) and a valid one is persisted (mock the provision call).

## 5. Per-repo substitutions
| | Edibl | HomeHoard |
|---|---|---|
| env prefix | `EDIBL_` | `HBOX_` |
| provision `app` name | `edibl` | `homehoard` |
| data-dir DSN file | `<EDIBL_DATA_DIR>/.database_url` | `<HBOX_DATA_DIR>/.database_url` |
| discovery helper to mirror | your `discover_*` (Supervisor `/addons`) | same |

## 6. Verify
`ruff` + full `pytest`; regenerate any config-doc artifact (myMeal's CI has a
"Configuration contract" check that fails if a new field isn't in the generated
`.env.example`/reference — make sure your equivalent doc-gen lists the two new
fields). Reviewer over the diff. Live check on HA: toggle on, confirm the app
provisions and boots on Postgres, and that removing `.database_url` re-provisions.

## 7. Reverse-port note (for myMeal, not you)
Edibl/HomeHoard's `sqlalchemy_uri` validates the URL **scheme + that the driver
is bundled** and raises a clear `ConfigError`; myMeal only warns. myMeal should
adopt that stricter check. (Tracked on the myMeal side.)
