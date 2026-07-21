# Changelog

All notable changes to the **myMeal add-on**. Home Assistant renders this file
in the add-on's Changelog tab, so entries are written for someone deciding
whether to hit Update — not for developers reading a diff.

Patch versions are minted automatically on every push to `main`; entries below
without notes are build-only republishes with no user-visible change.

## 1.1.5

Build-only republish; no user-visible change.

## 1.1.4

- **One-paste connect links.** The Home Assistant → API keys panel can now copy a
  **connect link** that bundles myMeal's address + the key, and the Edibl
  connection form accepts an Edibl connect link to fill its URL + token in one
  step — so pairing myMeal and Edibl standalone / across the network is a single
  paste instead of copying two fields. (Behind HA Ingress no key is needed.)

## 1.1.3

Build-only republish; no user-visible change.

## 1.1.2

- **Full undo parity for pantry actions.** Recording that food was eaten/spoiled
  and adding to Edibl's shopping list from myMeal's chat are now **undoable** too
  (previously only adding stock was) — every cross-app pantry change has a
  one-tap Undo, matching myMeal's own actions.

## 1.1.1

- **Undo pantry adds from myMeal's chat.** When myMeal adds stock to Edibl, the
  action chip now has a one-tap **Undo** (routed through a server proxy, since
  the browser can't reach Edibl directly).

## 1.1.0

- **One assistant for the whole kitchen.** When Edibl (the food-inventory app)
  is connected, myMeal's chat can now also **manage your pantry** — check what's
  on hand, add or use up stock, and add to Edibl's shopping list — not just read
  it. It's optional and auto-detected: with no Edibl connected these tools don't
  appear and standalone myMeal is unchanged.
- **More voice/Assist tools.** The MCP server can now **create recipes, plan and
  un-plan meals, and remove shopping items** (not just read), so Home Assistant
  Assist can manage myMeal end to end. Register Edibl's and HomeHoard's MCP
  servers alongside it and one Assist pipeline spans meals, pantry, and household
  inventory.
- **MCP server auth.** New `mcp_server_token` option — set a bearer token Home
  Assistant must present. Blank stays unauthenticated (safe only while the port
  is internal); set it before mapping port 7851 to your LAN.

## 1.0.6

Build-only republish; no user-visible change.

## 1.0.5

Build-only republish; no user-visible change.

## 1.0.4

Build-only republish; no user-visible change.

## 1.0.3

Build-only republish; no user-visible change.

## 1.0.2

Build-only republish; no user-visible change.

## 1.0.1

Build-only republish; no user-visible change.

## 1.0.0

Feature milestone (and first stable release).

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
