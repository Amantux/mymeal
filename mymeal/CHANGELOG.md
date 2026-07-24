# Changelog

All notable changes to the **myMeal add-on**. Home Assistant renders this file
in the add-on's Changelog tab, so entries are written for someone deciding
whether to hit Update — not for developers reading a diff.

## 1.1.31

- LLM-powered recipe builder + AI ingredient parsing/matching (Mealie-parity B).

## 1.1.30

- Parse ingredients into structured qty/unit on save (Mealie-parity A).

## 1.1.29

- Serving scaler + weight-based measurement view (features program, phase 1).

## 1.1.28

- Address import/assistant review (best-effort image, caps, tests).

## 1.1.27

- Import images+tags & let the assistant create recipes.

## 1.1.26

- Week view first day box larger — neutralize leaked .card margin.

## 1.1.25

- Meal plan day/week/month toggle + uniform day boxes.

## 1.1.24

Build-only republish; no user-visible change.

## 1.1.23

- Optional Ollama API key (mirror Edibl) for Cloud / secured instances.

## 1.1.22

- Phase 3 config-doc contract + review fixes.

## 1.1.21

- MyMeal find/register client for Shared PostgreSQL (Phase 3).

## 1.1.20

- Address Phase 2 review (credential read-back, silent rotation, isolation).

## 1.1.19

- Shared PostgreSQL add-on with per-app provisioning (Phase 2).

## 1.1.18

- Address Phase 1 review (percent-password crash + partial-boot heal).

## 1.1.17

- Harden auth — concurrency-safe provisioning + spoof-proof ingress peer.

## 1.1.16

- Near-live view sync via a central change-cursor poll.

## 1.1.15

- Live-update views (in-app signal, focus refetch, light poll).

## 1.1.14

Build-only republish; no user-visible change.

## 1.1.13

- **The Home Assistant integration connects reliably.** The add-on now hands the
  companion integration the exact address to reach it (its internal Home
  Assistant hostname) plus a dedicated, revocable API key, so setting up the
  integration "just works" from the auto-discovery prompt instead of failing to
  connect. (The add-on's sidebar was never affected.) Manage or revoke the key
  under **Settings → API keys**.

## 1.1.12

- Rebuild the dashboard into a supportive kitchen hub.

## 1.1.11

- Resilient async states, accessible modals & focus, cohesion.

## 1.1.10

- Harden SQLite for concurrent workers (WAL + busy_timeout + FKs).

## 1.1.9

Build-only republish; no user-visible change.

## 1.1.8

- **Manage API keys from Settings.** Creating, viewing, and revoking API keys
  (for the Home Assistant integration, the MCP server, or a companion app) is
  now in **Settings → API keys** — the obvious place — instead of tucked inside
  the Home Assistant page.
- **Make other people admins.** The first user can now promote other Home
  Assistant users to **admin** (or remove it) under **Settings → Household
  members**. Admins manage settings and keys; everyone else just uses the app.
  There's always at least one admin.
- **Fixed** a rare first-start crash on a brand-new database (a race between
  worker processes creating the tables).

## 1.1.7

- **API keys are now admin-only.** Minting or revoking keys is a household-admin
  action, consistent with the other settings.

## 1.1.6

- **"Find Edibl" is more reliable**, and now tells you *why* when it can't find
  it (e.g. the add-on needs the "manager" role, or you're not running under
  Home Assistant) instead of just failing. It also finds Edibl even when Home
  Assistant won't let add-ons list each other, by trying the usual addresses.

## 1.1.5

- **Everyone gets their own profile.** Behind Home Assistant, each signed-in HA
  user is now a distinct person in myMeal (so chat history is per-person)
  instead of one shared account — while still sharing the same recipes, plan,
  and shopping list. The first user becomes the admin.

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

- **Ports are internal-only by default.** myMeal is used via the sidebar
  (Ingress) and Home Assistant reaches the MCP server internally, so nothing is
  published to your network out of the box — the safer default. To allow LAN
  access, assign a host port for `7850` (API) or `7851` (MCP) in the add-on's
  **Network** tab.

## 1.0.5

- **"Find Edibl" now works on Home Assistant Supervised.** The add-on requests
  the Supervisor access it needs, and looks for Edibl on the right port. (If it
  still comes up empty, the URL can be entered manually.)

## 1.0.4

- **Connect Edibl in a couple of clicks.** New **Settings → Edibl** section with
  a **Find Edibl** button (auto-discovers the add-on — no token needed when both
  run behind Home Assistant), a **Test connection** button, and a manual URL for
  standalone setups.

## 1.0.3

- **Set up AI in the app.** Choose your provider (Claude, OpenAI, or Ollama) and
  enter its key/host right in **Settings** — remembered across restarts — instead
  of only via environment variables. Also settable from the add-on options.

## 1.0.2

- **Reuse the Ollama you already run for Home Assistant.** Clearer guidance and a
  helper to point myMeal at the same local Ollama server (no second copy of the
  model in memory).

## 1.0.1

- **Works on phones.** Added a navigation drawer so Settings and every page are
  reachable on mobile (previously the menu was hidden on small screens).

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

## 0.5.x

Pre-1.0 development builds: the initial recipe manager, meal planning, smart
shopping lists, the AI recipe importer and cooking chat, the Home Assistant
add-on / HACS packaging, and brand artwork. See the Git history for details.
