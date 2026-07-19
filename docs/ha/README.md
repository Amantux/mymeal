# myMeal + Home Assistant

myMeal integrates with Home Assistant three ways.

## 1. Add-on (recommended)

Add this repo under **Settings → Add-ons → Add-on Store → ⋮ → Repositories**,
install **myMeal**, and start it. Ingress serves the UI in the HA sidebar, and
the add-on advertises itself to HA so the companion integration is offered for
one-click setup ("New device discovered"). The add-on runs auth-disabled behind
ingress, so no API token is needed.

## 2. HACS integration (sensors, calendar, services, voice)

Installed automatically with the add-on, or add this repo as a custom HACS
integration repository. It creates:

- **Sensors** — recipes, meals planned this week, today's meals, shopping-list
  items, pantry items, pantry expiring within 7 days.
- **Calendar** — `calendar.mymeal_meal_plan`, your meal plan as HA calendar
  events.
- **Services** — `mymeal.whats_for_dinner`, `mymeal.what_can_i_cook`,
  `mymeal.add_to_shopping_list`, `mymeal.plan_week` (all support responses).
- **Lovelace card** — `custom:mymeal-card` (auto-registered).

For a standalone (non-add-on) myMeal with auth enabled, create an API key in
myMeal under **Home Assistant** and paste it during setup.

### Assist (voice)

Copy `custom_components/mymeal/custom_sentences/en/mymeal.yaml` to
`<config>/custom_sentences/en/mymeal.yaml` and restart HA. Then ask Assist:

- "What's for dinner?"
- "What can I cook right now?"
- "Add eggs to my shopping list."

## 3. MCP server (Assist via the Model Context Protocol)

The add-on also runs an MCP server on port `7851` (`/sse`). Add Home Assistant's
**MCP Client** integration pointing at `http://<addon-host>:7851/sse` to expose
richer cooking tools (search recipes, read steps aloud, plan the week, manage
the shopping list) to any Assist pipeline or LLM conversation agent.

## Dashboard

See [`overview_dashboard.yaml`](overview_dashboard.yaml) for a ready-to-paste
Lovelace dashboard.
