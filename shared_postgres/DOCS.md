# Shared PostgreSQL

A single PostgreSQL database that **myMeal**, **Edibl**, and **HomeHoard** can
share on Home Assistant, instead of each keeping its own SQLite file. Each app
gets its **own** database and login role — they never see each other's data, and
none get the superuser.

## How it works

- Runs Postgres 16 with its data persisted in the add-on's `/data` (survives
  restarts and updates).
- A small provisioning service publishes itself to Home Assistant so the sibling
  apps can **find and register** automatically: when you turn on "use shared
  Postgres" in myMeal / Edibl / HomeHoard, that app discovers this add-on, asks
  for its own database, and connects — no URLs to copy.

## Setup

1. Install and **Start** this add-on.
2. In each app you want to use it (myMeal, Edibl, HomeHoard), enable its
   "use shared Postgres" option. That's it.

You don't normally need to touch the options below.

## Options

- **`provision_token`** — a token every request must present. Left **blank**
  (default), a strong token is generated once and persisted; sibling apps receive
  it automatically via discovery, so you don't have to touch it. The endpoint is
  **never open** — this prevents another add-on from reading back an app's
  credentials. Set it explicitly only if you want to manage the token yourself.

## Ports

Both ports are **internal-only** by default — Home Assistant reaches them on its
internal network and nothing is exposed to your LAN. Map `5432/tcp` to a host
port only if you want to reach the database directly from another machine.

## Backups

This database lives in the add-on's `/data`, which Home Assistant includes in
add-on backups. Snapshot the add-on to back it up.

## Migrating existing data

Turning on shared Postgres gives an app a **fresh** database — it does not copy
your existing SQLite data. Start on Postgres before entering data you care about,
or migrate manually with `pg_dump`/`pg_restore` + your app's tools.

## Upgrading PostgreSQL major versions

PostgreSQL cannot open a database directory written by an older major version,
so moving from 16 to 18 is a real migration, not just a new image. The add-on
does it for you on first start after the update:

1. Your existing cluster is checked and, if the add-on was previously killed
   rather than stopped, recovered.
2. A new PostgreSQL 18 cluster is created with the **same** encoding, collation
   and checksum settings as your old one — `pg_upgrade` refuses to run if any of
   those differ.
3. `pg_upgrade` copies the data across. Copy mode, not link mode: it needs
   roughly as much free space again as the database currently uses, and the
   add-on checks that up front and refuses rather than filling `/data`.
4. The old cluster is **renamed, never deleted** — it stays at
   `/data/pgdata-old-16`.

**Rolling back.** Reinstall the previous add-on version; it will find the old
cluster. This is the only rollback path, which is why the folder is kept. Once
you're satisfied the upgrade went well, delete `/data/pgdata-old-16` to reclaim
the space.

**If it fails.** The add-on stops with the reason in its log and your original
database is untouched. It will not start PostgreSQL against a directory it
cannot read.

**Your own config.** If you hand-edited `postgresql.conf` or `pg_hba.conf`, the
upgraded cluster gets fresh ones. Your previous files are copied alongside as
`postgresql.conf.from-16` etc. for reference — they are **not** applied
automatically, because settings can be removed or renamed between majors.
