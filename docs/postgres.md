# Using Postgres instead of SQLite

myMeal defaults to a built-in **SQLite** database — zero-config, and the right
choice for a single household. You can instead point it at a **Postgres** server
(e.g. to share one database across apps, or for heavier concurrency). Schema is
managed by Alembic and runs identically on both backends.

## How to switch

Set the database URL; blank = SQLite.

```
MYMEAL_DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname
```

- **Standalone / remote (docker-compose):** an optional Postgres service ships in
  `docker-compose.yml`, off by default. Start it with the profile and uncomment
  the matching URL:
  ```
  docker compose --profile postgres up
  ```
- **Home Assistant — shared add-on (easiest):** install the **Shared PostgreSQL**
  add-on, then in myMeal set **Configuration → `use_shared_postgres`** on. myMeal
  discovers the add-on and gets its own database automatically — no URL to type.
  (Edibl and HomeHoard can share the same add-on the same way.) The provisioning
  token is auto-obtained via discovery; if your setup can't supply it, copy the
  add-on's token into `postgres_provision_token`.
- **Home Assistant — external Postgres:** alternatively set **`database_url`** to
  any reachable Postgres (a server on your LAN). Leave everything blank for SQLite.

## Notes

- Driver: `psycopg` (v3) is bundled in the image; the URL scheme is
  `postgresql+psycopg://`.
- Migrations run automatically at startup (`alembic upgrade head`). A fresh
  database is created from scratch; an existing SQLite install keeps working
  untouched (SQLite remains the default).
- **Re-provisioning:** with the shared add-on, myMeal remembers the database it
  was given (in `/data/.database_url`) and reuses it across restarts. If that
  database is removed or its credentials change and myMeal can no longer start,
  delete `/data/.database_url` and restart — it will discover and provision
  afresh. (Turning `use_shared_postgres` off also makes it ignore that file.)
- The database URL may contain a password — it's treated as a secret (never
  logged) and set via the masked add-on option or an env var / Docker secret.
- Back up Postgres yourself (it lives outside myMeal's `/data`; the Shared
  PostgreSQL add-on's data is in its own `/data` and is included in its backups).
