# myMeal 🍽️

**Self-hosted recipes, AI meal planning, pantry & smart shopping lists — with a
Home Assistant cooking assistant built in.**

myMeal is a privacy-first, self-hosted kitchen companion. Keep your recipe
collection, let an AI help you plan the week and cook, track what's in your
pantry, and generate consolidated shopping lists — all runnable standalone or as
a Home Assistant add-on with voice control through Assist.

> **Status:** all five milestones shipped — core recipe management, AI recipe
> import + pluggable providers, meal planning, pantry, smart shopping lists, the
> conversational cooking agent, the MCP server, and the Home Assistant
> integration. See the [CHANGELOG](CHANGELOG.md).

## Highlights (target feature set)

- 📖 **Recipe manager** — structured ingredients & steps, images, categories,
  tags, favorites, fast search.
- 🤖 **Pluggable AI** — Claude, Ollama, or OpenAI behind one interface. Import a
  recipe from a URL or pasted text, ask "what can I cook with what I have", and
  generate weekly meal plans.
- 🗓️ **Meal planning** — a weekly planner that also feeds your calendar.
- 🧺 **Pantry-aware** — track stock and get suggestions that minimize shopping.
- 🛒 **Smart shopping lists** — auto-built from your plan, deduped and grouped by
  aisle.
- 💬 **Cooking agent** — chat with an assistant that helps you build and cook
  recipes.
- 🔌 **Home Assistant native** — an MCP server exposes tools to Assist so you can
  ask "what's for dinner?" and manage your list by voice.

## Architecture

```
backend/          Flask API (app factory, /api/v1), SQLAlchemy models, MCP server
frontend/         Vue 3 + Vite SPA (served by the backend)
custom_components/ Home Assistant (HACS) integration — config flow, sensors, calendar, services, card
mymeal/           Home Assistant add-on config
Dockerfile        Multi-stage build (frontend → python runtime)
```

The backend serves both the JSON API under `/api/v1` and the built SPA. Auth is
JWT by default, or disabled (`MYMEAL_DISABLE_AUTH=true`) when running behind
Home Assistant ingress, which already authenticates the user.

## Running

### Docker (standalone)

```bash
docker compose up --build
# → http://localhost:7850
```

Set a strong `MYMEAL_SECRET_KEY` in `docker-compose.yml` before exposing it.

### Local development

```bash
# Backend
cd backend
pip install -r requirements.txt
python run.py            # http://localhost:7850

# Frontend (separate terminal, proxies /api to :7850)
cd frontend
npm install
npm run dev              # http://localhost:5173
```

### As a Home Assistant add-on

Add this repository in **Settings → Add-ons → Add-on Store → ⋮ → Repositories**,
then install **myMeal**. Ingress is enabled, and the MCP server is exposed on
port `7851` for the Home Assistant MCP Client.

## Configuration

All configuration is via `MYMEAL_`-prefixed environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `MYMEAL_SECRET_KEY` | `change-me…` | JWT signing key (set in production) |
| `MYMEAL_DISABLE_AUTH` | `false` | Single-tenant mode behind a trusted proxy / HA ingress |
| `MYMEAL_ALLOW_REGISTRATION` | `true` | Allow public sign-up |
| `MYMEAL_DATA_DIR` | `./data` | SQLite DB + uploaded images |
| `MYMEAL_PORT` | `7850` | HTTP port |
| `MYMEAL_AI_PROVIDER` | _(blank)_ | `claude` \| `ollama` \| `openai` (AI features) |

## Development

```bash
cd backend && ruff check . && pytest -q     # lint + tests
cd frontend && npm run build                # type/build check
```

## License

[AGPL-3.0](LICENSE). (myMeal is original, clean-room software; the license
choice mirrors the project owner's other self-hosted tools and can be changed.)
