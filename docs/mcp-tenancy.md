# MCP tenancy: single-household by design

## Decision

The MCP sidecar operates on **one household's data**, fixed at process start by
the single `MYMEAL_MCP_API_TOKEN` it uses for every REST call
(`backend/mcp_server.py`). This is a deliberate, supported limitation, not a
gap to close.

## Why this is safe in the supported deployment

myMeal ships as a Home Assistant add-on for **one household**. Behind HA
ingress with `disable_auth: true`, there is a single group, so "the sidecar's
group" and "the caller's group" are always the same. The ASGI guard still does
real work: it authenticates each MCP request and enforces the caller key's
**read/write access** (a read-only key cannot reach a write tool — see
`docs`/the guard and `api-token-auth-model`). What it does *not* do is switch
which group's data a tool touches, because there is only one.

## The unsupported mode, stated plainly

If someone runs a **single myMeal instance for several separate households**
(multiple groups) **and exposes MCP externally** (`mcp_expose_external: true`),
then a key issued to household B authenticates at the guard but the tool still
runs against the sidecar's fixed group A. That is cross-household access.

**This mode is unsupported.** Do not do it. The guard is per-key for
authentication and access class; it is not a per-tenant data boundary, and
making it one would require binding each request's caller identity through the
SSE transport to the REST call — which the transport's task model does not
allow cheaply (the tool runs in a different task than the authenticated POST).
The REST API itself is fully multi-tenant; only the MCP *sidecar* carries this
constraint, because it is a separate process holding one credential.

## If multi-tenant standalone MCP is ever wanted

It is a real project, not a patch: the sidecar would need to derive the caller's
group from the presented key (not a fixed token) and make that group ride every
REST call, with the guard verifying the key's group matches on each request.
Until then, the invariant is: **one instance, one household, or MCP stays
internal.**

Related: `api-token-auth-model` (scope × access), and the guard's
authentication-vs-authorization split (the read/write check applies on every
path, not only when externally exposed).
