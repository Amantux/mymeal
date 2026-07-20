# Changelog

All notable changes to the **myMeal add-on**. Home Assistant renders this file
in the add-on's Changelog tab, so entries are written for someone deciding
whether to hit Update — not for developers reading a diff.

Patch versions are minted automatically on every push to `main`; entries below
without notes are build-only republishes with no user-visible change.

## 1.0.0

Build-only republish; no user-visible change.

## 0.6.0

Feature milestone.

### Added
- **Companion Edibl integration.** myMeal now works with the Edibl food-inventory
  app: it can pull your real, fresh stock and push planned ingredients for
  reconciliation. Configure `MYMEAL_EDIBL_URL` (and a token).
- **Ambient cooking assistant.** A floating chat button on every page, with
  quick-start suggestions and one-tap **undo** for actions it takes (e.g.
  adding to your shopping list).
- **One typed, validated configuration contract** with a `config_check` command,
  clearer startup diagnostics, and safer secret handling.

### Changed
- **myMeal no longer keeps its own pantry** — inventory is owned by Edibl.
  Inventory-aware features ("what can I cook") use Edibl when connected and are
  otherwise cleanly unavailable. **On upgrade, the old local pantry table is
  removed and its rows are discarded** (that data now lives in Edibl).

## 0.5.12

Build-only republish; no user-visible change.

## 0.5.11

Build-only republish; no user-visible change.

## 0.5.10

Build-only republish; no user-visible change.

## 0.5.9

Build-only republish; no user-visible change.

## 0.5.8

Build-only republish; no user-visible change.

## 0.5.7

Build-only republish; no user-visible change.

## 0.5.6

Build-only republish; no user-visible change.

## 0.5.5

### Added
- Add-on documentation (this changelog and the Documentation tab), plus
  one-click **Add to My Home Assistant** buttons for both the add-on repository
  and the HACS integration.

## 0.5.4

### Added
- Brand artwork: add-on icon and logo, plus a banner used by the repository and
  the HACS store panel.

### Fixed
- Declared the `http` dependency in the integration manifest. Without it, Home
  Assistant could set up myMeal *before* `http` was ready, and the integration
  would fail to load while serving the Lovelace card.
- Removed an invalid key from `hacs.json` that made HACS validation fail.
- Corrected integration manifest key ordering to satisfy hassfest.

## 0.5.3

### Fixed
- **No sign-in prompt behind ingress.** The web UI previously *inferred* whether
  login was required from whether an unauthenticated request happened to
  succeed. Any transient error dropped you onto a login screen — which is
  meaningless inside Home Assistant, since HA has already authenticated you.
  The backend now states its auth mode explicitly and the UI honours it.

  Auth is unchanged when running outside Home Assistant: it stays fully on.

## 0.5.2

### Changed
- Repository moved to `github.com/Amantux/mymeal`; the container image is now
  published as `ghcr.io/amantux/mymeal`. Earlier image references pointed at a
  namespace that did not exist and could not be pulled.

## 0.5.0 — first public release

### Added
- **Recipes** — structured ingredients and steps, images, categories, tags,
  favourites, and search.
- **AI meal planning** — weekly plan generation, pantry-aware "what can I cook",
  and recipe import from a URL. Pluggable across Claude, Ollama, and OpenAI;
  entirely optional, and off unless you configure a provider.
- **Pantry** — track what you have, with expiry awareness.
- **Smart shopping lists** — built from your meal plan, deduplicated and grouped
  by aisle.
- **Cooking assistant** — a chat agent that helps you build and cook recipes.
- **Home Assistant integration** — ingress web UI with no separate login, an MCP
  server for Assist voice control, a meal-plan calendar entity, sensors,
  services, custom sentences, and a Lovelace card.
