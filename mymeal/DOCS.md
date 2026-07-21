# myMeal — Home Assistant add-on

Recipes, AI meal planning, and smart shopping lists — running inside
Home Assistant, with voice control through Assist.

[![Add repository to My Home Assistant][repo-badge]][repo-link]

## Install

1. Click the button above (or **Settings → Add-ons → Add-on Store → ⋮ →
   Repositories** and paste `https://github.com/Amantux/mymeal`).
2. Find **myMeal** in the store and click **Install**.
3. Click **Start**, then **Open Web UI**.

There is nothing else to configure. The defaults are the intended setup.

## You will not be asked to sign in

The add-on runs behind Home Assistant **ingress**: HA authenticates you before
the request ever reaches myMeal, so a second login would be pure friction. The
add-on therefore ships with `disable_auth: true`, and the web UI asks the
backend directly whether sign-in is required rather than guessing.

**This is safe here and only here.** With `disable_auth` on, every request binds
to a single local user. That is correct behind ingress, where HA is the
gatekeeper. It is *not* safe on a port exposed to your network — see
"Running outside Home Assistant" below.

## Configuration

| Option | Default | What it does |
|---|---|---|
| `disable_auth` | `true` | No login prompt behind ingress. Turn OFF only if you expose port 7850 directly and want myMeal's own login. |
| `allow_registration` | `false` | Whether strangers can create accounts. Irrelevant while `disable_auth` is on. |
| `enable_mcp` | `true` | Runs the MCP server on port 7851 so Assist can use myMeal as a tool. |
| `ai_provider` | _(blank)_ | `claude`, `ollama`, or `openai`. Blank = AI features off; everything else still works. |

### Turning on the AI features

myMeal works fully without AI — you just lose recipe-import-from-URL,
"what can I cook", plan generation, and the chat assistant.

Configuration is by environment / add-on options (the Settings page shows
provider status but does not edit it):

- **`ollama`** — fully local, no API key, nothing leaves your network. This is
  the privacy-preserving choice.

  **Already running Ollama for Home Assistant's conversation agent? Reuse it —
  it's the same server.** Home Assistant's Ollama integration connects *out* to
  an Ollama server and does not re-share that model with other apps, so myMeal
  cannot route *through* Home Assistant — but it can talk to the very same
  Ollama directly. One server, both consumers, no duplicate model in memory.

  Point myMeal at it:

  ```
  MYMEAL_AI_PROVIDER=ollama
  MYMEAL_OLLAMA_HOST=http://<your-ollama-host>:11434
  ```

  Use the same host you gave the HA Ollama integration — often the Ollama
  add-on's hostname (e.g. `http://local-ollama:11434`), `http://homeassistant.local:11434`,
  or the Docker host. If myMeal can't reach it, Ollama is probably bound to
  loopback only; start it with `OLLAMA_HOST=0.0.0.0` so other containers can
  connect.

  (There is also a helper endpoint, `GET /api/v1/ai/discover-ollama`, that probes
  those addresses and reports what it finds — handy for confirming the host to
  put in `MYMEAL_OLLAMA_HOST`.)
- **`claude`** / **`openai`** — better quality, but recipe text and your
  question are sent to that provider. Requires an API key (`MYMEAL_ANTHROPIC_API_KEY`
  / `MYMEAL_OPENAI_API_KEY`) and costs money per request.

Recipe import tries the page's embedded structured data **first** and only falls
back to the AI when a site doesn't publish any — so most imports cost nothing
even with a paid provider configured.

## Connect Edibl (food inventory)

If you also run the companion **Edibl** app, connecting it powers
inventory-aware cooking — "what can I cook right now?" from your real, fresh
stock.

**Easiest path (both as Home Assistant add-ons):** open myMeal → **Settings →
Edibl** → **Find Edibl**. myMeal asks the Supervisor for the Edibl add-on and
fills in its address. Because both run behind ingress, **no token is needed** —
click **Save** and you're connected. **Test connection** confirms it.

Running Edibl standalone (with its own login)? Enter its URL and paste an Edibl
API token (Edibl → its tokens screen). The token is stored on this server only
and never shown again.

## Voice control with Assist

The add-on runs an MCP server on port `7851`. To let Assist cook with it:
**Settings → Devices & Services → Add Integration → Model Context Protocol**,
then point it at the SSE endpoint.

By default the port is **internal only** — Home Assistant reaches it over its
own network, so use the add-on's hostname:

```
http://<mymeal-addon-hostname>:7851/sse
```

Prefer a fixed address, or connecting from another machine? Open the add-on's
**Network** tab, give port `7851` a host port, and use
`http://<your-ha-host>:7851/sse` instead.

Ask things like *"what's for dinner?"*, *"add milk to my shopping list"*, or
*"what can I cook with what I have?"*.

For entities (sensors, a meal-plan calendar, services), also install the
companion **myMeal integration** from HACS — see the repository README. The
integration auto-discovers this add-on, so you should not need to type a URL.

## Network & ports

Out of the box myMeal is **fully internal**: the web UI is reached through the
Home Assistant sidebar (ingress), and the MCP server is reached by Home
Assistant over its internal network. **No ports are published to your network**,
which is the recommended, most secure setup.

If you want direct LAN access, open the add-on's **Network** tab and assign a
host port:

| Port | Give it a host port when you want to… |
|---|---|
| `7850` | reach the REST API directly from the LAN, or let the HACS integration connect over the network instead of the internal one |
| `7851` | reach the MCP server from a machine other than Home Assistant |

Leave a port blank to keep it internal-only. You can change the host port here
at any time.

## Data and backups

Everything lives in the add-on's data directory: a SQLite database plus uploaded
recipe images. It is included in normal **Home Assistant backups** — take one
before upgrading if your collection matters to you.

Nothing is sent anywhere unless you explicitly configure a cloud AI provider.

## Running outside Home Assistant

The same image runs standalone via `docker compose` (see the repository README).
In that mode **auth stays on**: `MYMEAL_DISABLE_AUTH` defaults to `false`, and
you should set a strong `MYMEAL_SECRET_KEY`. Never set `MYMEAL_DISABLE_AUTH=true`
on anything reachable from an untrusted network.

## Troubleshooting

**"Open Web UI" shows a login screen.**
That should not happen behind ingress. Check that `disable_auth` is still `true`
in the add-on configuration, then restart the add-on.

**The add-on won't start.**
Check **Log** in the add-on page. The most common cause is port `7851` already
being in use by another add-on; set `enable_mcp: false` to free it.

**AI features return an error.**
Confirm `ai_provider` matches the credentials you configured (env / add-on
options). For Ollama, confirm the add-on can reach your Ollama host — it is a
separate service
and is not bundled here.

**Assist can't see myMeal.**
Confirm `enable_mcp: true`, the add-on is running, and the MCP integration URL
uses your HA host's real address (not `localhost`, which resolves inside the
container, not on your network).

## Support

Issues and questions: <https://github.com/Amantux/mymeal/issues>

[repo-badge]: https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg
[repo-link]: https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FAmantux%2Fmymeal
