# Add-on ↔ companion integration pairing (best practice)

> **Portable spec.** Written for myMeal, but deliberately app-agnostic so it can
> be carried over to **Edibl** (or any HA add-on + companion HACS integration
> pair). Anywhere it says *the add-on* / *the integration*, substitute the app.
> The **Edibl carry-over checklist** at the end lists exactly what changes.

## 1. The problem this solves

An HA **add-on** and its companion **integration** reach the backend through two
completely different doors:

- **The add-on (sidebar) uses Ingress**: browser → Home Assistant → Supervisor →
  the add-on's `ingress_port`. Ingress needs **no published host port and no
  token** — the Supervisor authenticates the user and injects
  `X-Remote-User-*` identity headers.
- **The integration does NOT use Ingress.** It is Python running *inside Home
  Assistant Core* that must open a **direct HTTP connection** to the add-on's
  REST API. Ingress is invisible to it.

So an integration that "works in the browser but not as an integration" almost
always has one of these two faults:

1. **Wrong address.** `http://127.0.0.1:<port>` points at *Home Assistant Core's
   own container*, not the add-on. And the add-on's **container hostname**
   (`os.environ["HOSTNAME"]` / `socket.gethostname()`) is a Docker container ID,
   **not** the DNS name other containers resolve.
2. **Auth mismatch.** The API is either wide open (relying on the ingress
   fallback), or it rejects the integration because the integration presents no
   credential the backend honors on the direct path.

Publishing a host port is **not** required and is **not** the fix — containers
on the `hassio` network reach each other on any listening port regardless of the
`ports:` mapping. Publishing is only for LAN access from another machine.

## 2. Target architecture

```
Browser ──Ingress──▶ Supervisor ──▶ add-on :ingress_port   (X-Remote-User-* identity)
HA Core (integration) ──direct HTTP──▶ http://<addon-dns-host>:<api-port>   (Bearer <api-key>)
```

The companion integration is a normal **polling custom integration** (config
entry + coordinator + entities/services). This is the idiomatic HA pattern when
the app exposes rich objects (a calendar, sensors, services) — MQTT discovery
and "push states via the Supervisor Core API" cannot express those cleanly.

Three requirements make it correct and secure:

| # | Requirement | Why |
|---|---|---|
| R1 | Integration authenticates with a **long-lived API key** (Bearer) | Identified, revocable, works even when the internal API is later locked down |
| R2 | The add-on **advertises the Supervisor hostname**, not the container hostname | Only the Supervisor-assigned name (`local-<slug>` / `<repo>-<slug>`) is DNS-resolvable by HA Core |
| R3 | Backend auth **honors a Bearer token in every mode** | So the token works whether or not `disable_auth` is set; ingress and token are independent auth sources |

## 3. Backend contract

### 3.1 Auth resolution order (R3)

`load_current_user()` resolves identity in this fixed order, so the three
sources are independent and a single `DISABLE_AUTH` toggle only controls the
open fallback:

1. **`Authorization: Bearer <token>`** present → authenticate by it (long-lived
   API key *or* login JWT). A **present-but-invalid** token is a `401` — never a
   silent downgrade to the shared user.
2. **Trusted ingress identity** → `X-Remote-User-*`, but *only* when the request
   actually came from the Supervisor ingress peer (see the trust boundary
   below). Provisions/returns the per-HA-user account.
3. **`DISABLE_AUTH` (open mode) only** → the shared local user. This covers both
   a standalone open deployment and an ingress request that arrived without
   identity headers.
4. Otherwise → `None` (→ `401`).

> **Intentional behavior change:** step 2 runs *regardless* of `DISABLE_AUTH`.
> Previously, an auth-enabled deployment honored **only** a Bearer token and
> ignored `X-Remote-User-*` entirely. Now a trusted-peer ingress request
> authenticates even with auth enabled — this is what makes "hardened mode" (§7)
> usable behind ingress. It only affects installs behind real HA ingress; a
> standalone auth-enabled server never sees `remote_addr == 172.30.32.2`.

**Ingress trust boundary (unchanged, do not weaken):** trust `X-Remote-User-*`
only when `request.remote_addr` is exactly the Supervisor peer `172.30.32.2` —
never a `/23` (the bridge gateway `.1` SNATs host-published ports) — and disable
this trust when a reverse proxy is trusted (`TRUSTED_PROXY_COUNT > 0`), because
`ProxyFix` then derives `remote_addr` from a client-supplied header.

### 3.2 Endpoints the integration uses

- `GET /api/v1/ha/summary` — poll target for the coordinator (health + counts +
  today/this-week). Used by config-flow validation too.
- `GET /api/v1/ha/calendar` — meal-plan (or app-equivalent) calendar entity.
- Plus the app's service endpoints for the integration's `services.yaml`.

All are `login_required`; with R3 a valid Bearer key satisfies them.

## 4. Integration token lifecycle (R1)

- **Mint once, at startup**, bound to the shared household owner (the app's
  `_default_user()` — always creatable, and all HA users share one group, so the
  household's data is fully visible to it).
- **Persist the RAW token** to a private file in the data dir
  (`$DATA_DIR/.integration_token`, mode `0600`). Rationale:
  - **Restart-stable**: re-minting on every boot would invalidate the token the
    configured integration already stored, breaking it. Reuse if the file still
    matches a live DB record.
  - **Handoff**: the discovery step is a *separate process* from the app; the
    file is how it obtains the raw value to advertise.
- **Secret hygiene**: never log the raw value; only a `hint` (first 7 chars) is
  stored in the DB, and only a SHA-256 hash is used for lookup. If the raw file
  is lost, existing integrations keep working (the DB hash still matches the
  token they hold); a fresh mint only affects *new* setups.
- **Revocable** from **Settings → API keys** like any other key.

## 5. Discovery payload (R2)

At startup, after the token is ensured, the add-on POSTs to the Supervisor:

```
POST http://supervisor/discovery
Authorization: Bearer $SUPERVISOR_TOKEN
{
  "service": "<domain>",                     // e.g. "mymeal" — matches config.yaml `discovery:`
  "config": {
    "host":  "<supervisor hostname>",        // GET /addons/self/info → data.hostname
    "port":  <api-port>,                     // internal API port (needs no host mapping)
    "token": "<raw integration api key>"     // from §4
  }
}
```

- **Hostname**: `GET http://supervisor/addons/self/info` → `data.hostname`. Fall
  back to the container hostname only if that call fails (best-effort, logged).
- Requires `hassio_api: true` in `config.yaml` (already needed for sibling
  discovery). The default `hassio_role` is sufficient for `self/info`.

## 6. Config flow

- **hassio (auto-discovery)**: read `host`, `port`, `token` from
  `discovery_info.config`; validate against `GET /ha/summary` **with** the token;
  store all three in the entry. (Previously stored an empty token — that only
  worked because the open fallback caught it.)
- **manual**: host + port + optional token. Default host stays `http://127.0.0.1`
  for the **standalone same-host** case; add-on users who set it up by hand use
  the add-on DNS host (`http://local-<slug>:<port>`). Validation maps `401/403`
  → `invalid_auth`, connection failures → `cannot_connect`.
- **options**: poll interval only.

## 7. Security posture & the `disable_auth` decision

`disable_auth: true` remains the **default** so existing installs don't change
behavior: the browser (ingress) path is unaffected and the open fallback stays.
With R3 in place the integration is now *properly authenticated by its token*
regardless of this flag — so the token is no longer relying on the open door.

**Hardened mode (opt-in, one line):** set `disable_auth: false`. Then:
- Browser behind ingress still works (trusted `X-Remote-User-*`, step 2).
- Integration + MCP still work (their tokens, step 1).
- **Unauthenticated internal callers get `401`** (step 4) — the internal API is
  no longer world-readable on the `hassio` network.
- Trade-off: if HA ever omits identity headers behind ingress, that request
  `401`s instead of silently becoming the shared user. In practice HA always
  passes identity for a logged-in user.

Recommend hardened mode for multi-add-on setups; keep the default for maximum
compatibility.

## 8. Edibl carry-over checklist

Everything above is app-agnostic. To port to Edibl, substitute:

- [ ] **Domain / service name** (`mymeal` → `edibl`) in `config.yaml`
      `discovery:` and the discovery POST `service`.
- [ ] **API port** (myMeal `7850` → Edibl's app port).
- [ ] **Poll endpoint(s)** — Edibl's equivalent of `/ha/summary` (+ calendar if
      any) and its service endpoints.
- [ ] **Backend auth**: apply the §3.1 resolution order. If Edibl already shares
      myMeal's `auth.py` shape (ingress trust + API tokens), this is the same
      reorder; keep the `172.30.32.2` trust boundary intact.
- [ ] **Token module**: port `ensure_integration_token()` (§4) — same logic,
      Edibl's data dir + models.
- [ ] **Discovery**: port the `self/info` hostname lookup + token in the payload
      (§5). Requires `hassio_api: true`.
- [ ] **Config flow**: consume `{host, port, token}` (§6).
- [ ] Confirm `hassio_api: true` and that the integration's `async_step_hassio`
      is wired for the add-on's `discovery:` service name.
- [ ] Decide the `disable_auth` default per §7 (recommend matching myMeal's).

## 9. Verification

1. **Unit**: a Bearer API key authenticates even with `DISABLE_AUTH=true`; an
   invalid Bearer is `401` (not the shared user); ingress identity still resolves
   with a valid peer; `ensure_integration_token()` is idempotent across calls.
2. **Discovery**: on the add-on, confirm `GET /addons/self/info` returns a
   `hostname`, and the posted discovery `config` carries host+port+token.
3. **End-to-end**: from HA Core, `curl http://<addon-hostname>:<port>/api/v1/ha/summary`
   with the token → `200`; without it in hardened mode → `401`.
4. **Round-trip**: remove + re-add the integration via the discovered flow; the
   entry sets up without manual entry and survives an add-on restart (stable
   token).
