# Changelog

## 0.2.0 — AI recipe import + provider layer (Milestone 2)

### Added
- **Pluggable AI provider layer** (`services/ai/`): one interface, three
  adapters — **Claude** (`claude-opus-4-8`), **OpenAI**, and **Ollama** (local).
  Selected via `MYMEAL_AI_PROVIDER`; `GET /api/v1/ai/providers` reports status.
- **Recipe import** (`POST /api/v1/ai/import`): imports from a URL or pasted
  text. URLs try deterministic schema.org/JSON-LD extraction first (no tokens),
  falling back to the AI provider; text always uses the provider.
- **Frontend**: an Import view (link or paste) and a Settings view showing
  provider status.
- Tests for JSON-LD extraction, ISO-8601 duration parsing, and both import
  paths using a fake provider (no network/keys).

## 0.1.0 — Foundation (Milestone 1)

Initial scaffold of **myMeal**, a self-hosted recipe manager, AI meal planner,
and Home Assistant cooking assistant.

### Added
- **Backend** (Flask + SQLAlchemy 2.0): app factory, `MYMEAL_`-prefixed config,
  JWT auth with a `MYMEAL_DISABLE_AUTH` ingress mode, and long-lived API keys.
- **Data model**: recipes (with structured ingredients & steps), foods, units,
  categories, tags, users, groups — multi-tenant by group.
- **REST API** under `/api/v1`: recipe CRUD (with slug lookup, favorites, image
  upload), foods/units, categories, tags, cross-entity search, and token/user
  management.
- **Frontend** (Vue 3 + Vite + Pinia): dashboard, recipe browser, recipe
  view/edit, login/registration, and a Home Assistant setup page.
- **Packaging**: multi-stage Dockerfile, unified entrypoint (standalone + HA
  add-on), docker-compose, HA add-on `config.yaml`, HACS metadata, and CI
  (tests + lint + frontend build + add-on validation).

### Not yet included (planned)
- AI recipe import & pluggable providers (Claude / Ollama / OpenAI) — M2
- Meal planning, pantry, and smart shopping lists — M3
- Conversational cooking agent — M4
- MCP server & Home Assistant integration (sensors, calendar, Assist) — M5
