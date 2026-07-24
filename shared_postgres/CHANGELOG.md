# Changelog

## 0.1.0

- Initial release. A shared PostgreSQL 16 database for myMeal, Edibl, and
  HomeHoard, with automatic per-app provisioning (each app gets its own database
  and least-privilege role) and Home Assistant discovery so the apps can find and
  register with it without manual connection strings. Data persists in the
  add-on's `/data`; ports are internal-only by default.
