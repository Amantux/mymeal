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
- **Home Assistant add-on:** set **Configuration → `database_url`** to a reachable
  Postgres — a separate Postgres add-on on the same box, or a server on your LAN.
  (An HA add-on can't launch its own database container; that's why the DB is a
  separate add-on/host.) Leave blank to keep SQLite.

## Notes

- Driver: `psycopg` (v3) is bundled in the image; the URL scheme is
  `postgresql+psycopg://`.
- Migrations run automatically at startup (`alembic upgrade head`). A fresh
  database is created from scratch; an existing SQLite install keeps working
  untouched (SQLite remains the default).
- The database URL may contain a password — it's treated as a secret (never
  logged) and set via the masked add-on option or an env var / Docker secret.
- Back up Postgres yourself (it lives outside the add-on's `/data`).

> Coming next: an optional **Shared PostgreSQL** add-on with a one-tap
> find-and-register flow, so myMeal / Edibl / HomeHoard can share one database on
> Home Assistant without manual URLs.
