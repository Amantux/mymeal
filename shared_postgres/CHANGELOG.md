# Changelog

## 0.2.0

- **PostgreSQL 18** (was 16). Your existing database is upgraded in place on
  first start — nothing to do, and no data is copied out or re-entered.
- The upgrade keeps your old PostgreSQL 16 cluster at `/data/pgdata-old-16`
  rather than deleting it, so you can go back by reinstalling the previous
  add-on version. Delete that folder once you're happy, to reclaim the space.
- If the upgrade can't run — not enough free space in `/data`, or something
  unexpected — the add-on stops with an explanation and **leaves your PostgreSQL
  16 database exactly as it was**, rather than starting against a database it
  cannot read.

## 0.1.0

- Initial release. A shared PostgreSQL 16 database for myMeal, Edibl, and
  HomeHoard, with automatic per-app provisioning (each app gets its own database
  and least-privilege role) and Home Assistant discovery so the apps can find and
  register with it without manual connection strings. Data persists in the
  add-on's `/data`; ports are internal-only by default.
