# openbb_snaptrade

SnapTrade backend for OpenBB Workspace. Custom widgets (Connection Portal, Portfolio Overview, Activity, Reference Data, Market Data, Order Ticket) backed by the SnapTrade REST API, plus an embedded MCP server that lets the Workspace AI agent query the same data on the user's behalf.

## Developer Quick Start

`docker compose up -d` runs three containers:

- **app** — FastAPI on port `8069` (compose-internal only). Hosts both the widget HTTP API and the MCP server mounted at `/mcp`.
- **tls-proxy** — Caddy terminating TLS on `https://localhost:8443` (the only host-published port) and reverse-proxying to `app`.
- **redis** — backs `pywry.state.redis.RedisSessionStore`, which holds the per-user session record (encrypted SnapTrade credentials + sliding 15-minute TTL).

The app port is intentionally not published — requests must flow through Caddy so the auto-injected CSP `frame-src` rules work, Workspace can load the iframes, and the MCP DNS-rebinding protection can see the real `Origin` / `Host`.

### Run it

1. Create `.env` at the repo root:

   ```bash
   SNAPTRADE_STORE_ENCRYPTION_KEY_B64=$(python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())")
   SNAPTRADE_CONNECTION_REDIRECT=https://localhost:8443/widget
   ```

   The SnapTrade client ID and consumer key are **not** env vars — they are sent per request by OpenBB Workspace as headers (see step 4).

2. Start the stack:

   ```bash
   docker compose up -d --build
   ```

3. Trust Caddy's local CA once (browser will otherwise refuse the iframe):

   ```bash
   docker compose exec tls-proxy cat /data/caddy/pki/authorities/local/root.crt > caddy-root.crt
   # macOS: sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain caddy-root.crt
   # Linux: sudo cp caddy-root.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates
   ```

4. In OpenBB Workspace, add the backend:

   `https://localhost:8443/`

   And set these auth headers:

   - `x-openbb-snaptrade-client-id` → your SnapTrade client ID
   - `x-openbb-snaptrade-consumer-key` → your SnapTrade consumer key

   The MCP server is auto-attached. Each iframe widget advertises `storage.mcpUrl = https://<host>/mcp/u/<token>`, where `<token>` is a freshly-minted HMAC-signed session token unique to this user.

## Environment variables

Required (compose fails if missing):

| Variable | Purpose |
|---|---|
| `SNAPTRADE_STORE_ENCRYPTION_KEY_B64` | Base64-encoded 32-byte key used to AES-256-CBC encrypt every Redis value (SnapTrade `user_id`/`user_secret` plus the per-session credential blob stored inside `UserSession.metadata`). Rotating it invalidates every stored credential and every live session token. |
| `SNAPTRADE_CONNECTION_REDIRECT` | Where SnapTrade's Connection Portal sends the browser back to after the user links a broker. Must point to the `/widget` route on this service.|

Optional:

| Variable | Default | Effect |
|---|---|---|
| `SNAPTRADE_AUTH_SECRET` | derived from `SNAPTRADE_STORE_ENCRYPTION_KEY_B64` | HMAC secret used by `pywry.state.auth.generate_session_token` / `validate_session_token`. Set explicitly if you want to rotate session-token signatures independently of the at-rest encryption key, or to share signatures across multiple app replicas. |
| `SNAPTRADE_DEBUG_HEADERS` | `0` | `1` logs every incoming request's headers. Useful for diagnosing what Workspace actually forwards. Disable in production. |
| `SNAPTRADE_MCP_ALLOW_LOOPBACK` | `1` | Allow MCP requests whose `Origin` is `http(s)://localhost` / `127.0.0.1` / `[::1]`. Set to `0` in production so only `https://*.openbb.<tld>` can reach the MCP endpoint. |

## Identity and credentials

This service has no application login and no env-var SnapTrade credentials. Every request must carry three headers from OpenBB Workspace:

- `x-openbb-snaptrade-client-id`
- `x-openbb-snaptrade-consumer-key`
- `x-openbb-user` — any stable per-user identifier

Without them every `/snaptrade/*` route returns 404. Each user's SnapTrade `user_id`/`user_secret` is encrypted with `SNAPTRADE_STORE_ENCRYPTION_KEY_B64` and stored in Redis keyed by `sha256(x-openbb-user)`.

If the supplied client ID starts with `PERS-` (case-insensitive), the app treats it as a personal-tier credential and skips per-user SnapTrade registration. Multi-tenant deployments must use a non-personal client ID.

Put your own access control (Workspace SSO, VPN, IP allowlist, etc.) in front of this service if it's reachable from anywhere untrusted.

## Session model

The iframe HTML is rendered with **zero** credentials embedded. `View Source` on any widget shows only an opaque HMAC-signed session token in the URL path (`/widget/s/<token>` for iframes, `/mcp/u/<token>` for the MCP endpoint).

Sessions are managed by `pywry.state.auth` + `pywry.state.redis.RedisSessionStore` — see [`openbb_snaptrade/auth.py`](openbb_snaptrade/auth.py).

Flow per Workspace dashboard load:

1. Workspace fetches `GET /widgets.json` with the three `x-openbb-*` headers. The backend calls `SESSION_MANAGER.mint(client_id, consumer_key, x-openbb-user)` which:
   - Generates a signed token via `pywry.state.auth.generate_session_token(user_id=sha256(email), secret=SNAPTRADE_AUTH_SECRET, expires_at=now+15min)`. Token shape: `user_id:created_ts:expiry_ts:hmac_sha256_signature`.
   - AES-256-CBC encrypts `{clientId, consumerKey, email}` and writes it to `RedisSessionStore` as `UserSession.metadata.enc`, keyed by `session_id = sha256(email)`, with a 15-minute TTL.
   - Rewrites every iframe `endpoint` to `https://<host>/<widget>/s/<token>` and every `storage.mcpUrl` to `https://<host>/mcp/u/<token>`.
2. The iframe HTML loads. Its JS reads the token from `window.location.pathname` and sends `Authorization: Bearer <token>` on every backend call to `/snaptrade/*`.
3. The agent's MCP client POSTs to `https://<host>/mcp/u/<token>/`. A path-extraction middleware lifts the token out of the URL into a per-request `ContextVar`, the gated ASGI wrapper validates the `Origin`, and `SESSION_MANAGER.resolve(token)`:
   - Verifies the HMAC signature with `validate_session_token` (rejects forged or tampered tokens).
   - Loads the session from Redis, decrypts the credential blob, calls `RedisSessionStore.refresh_session(session_id, extend_ttl=15min)` to keep the session alive, and returns a `WorkspaceContext`.
   - If the signature is invalid, the session is missing, or the TTL has expired, MCP tools return `{"error": "no_active_session"}`.

Tokens are HMAC-SHA256 signed and embed their own expiry. They are scoped per-user (`session_id = sha256(email)`), so opening Workspace as a different `x-openbb-user` mints an entirely separate session. The original `client_id` / `consumer_key` never leave the backend's Redis once written, and they never reach the browser.

## MCP server (`/mcp/u/<token>`)

The MCP server is mounted onto the same FastAPI app and exposes four tools: `list_connections`, `list_accounts`, `get_account_summaries`, `get_portfolio_exposure`. The Workspace AI agent auto-connects to it because every iframe widget in `widgets.json` advertises `storage.mcpUrl = https://<host>/mcp/u/<token>` — a user-scoped URL minted by the backend.

Why the token is in the URL path: OpenBB Workspace does **not** forward the backend-configured headers (`x-openbb-snaptrade-*`, `x-openbb-user`) to a widget's `mcpUrl`. The agent's MCP client only sends standard MCP/CORS headers. Encoding the per-user session token directly in the URL is the only reliable channel.

The MCP endpoint enforces three independent gates before any tool runs:

1. **Origin allowlist** — a custom ASGI wrapper rejects the request with HTTP 403 unless `Origin` is present and its hostname matches `^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.openbb\.[a-z]{2,}$` over HTTPS. Loopback origins are accepted only while `SNAPTRADE_MCP_ALLOW_LOOPBACK=1`. The regex is anchored — `openbb.dev.attacker.com` is rejected.
2. **FastMCP DNS-rebinding protection** (built-in) — additionally validates `Host` against `pro.openbb.co` / loopback. Configured in [`openbb_snaptrade/mcp_server.py`](openbb_snaptrade/mcp_server.py) via `TransportSecuritySettings`; add your own Workspace host there if you deploy your own.
3. **HMAC-signed session token** — `SESSION_MANAGER.resolve(token)` validates the signature with `validate_session_token`, looks up the session in Redis, and refreshes its 15-minute sliding TTL. Forged tokens, tokens for users who never opened the dashboard, and tokens whose session has expired all result in `no_active_session`.

Inside the request, MCP tools never see any custom HTTP headers — they read the resolved `WorkspaceContext` (containing the decrypted `client_id` / `consumer_key`) from a `ContextVar` set by the gated wrapper, and use it to call the SnapTrade SDK. The original `client_id` / `consumer_key` are written to Redis once at `/widgets.json` time and never leave the backend.

To verify the gates from a host shell:

```bash
# Mint a token (simulating Workspace's widgets.json fetch)
TOKEN=$(curl -sk https://localhost:8443/widgets.json \
  -H 'x-openbb-snaptrade-client-id: PERS-…' \
  -H 'x-openbb-snaptrade-consumer-key: …' \
  -H 'x-openbb-user: someone@example.com' \
  | python -c 'import sys,json,re; d=json.load(sys.stdin); print(re.search(r"/mcp/u/(.+)$", next(w["storage"]["mcpUrl"] for w in d.values() if isinstance(w,dict) and isinstance(w.get("storage"),dict) and "mcpUrl" in w["storage"])).group(1))')

# 403 — no Origin
curl -sk -o /dev/null -w '%{http_code}\n' \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'mcp-protocol-version: 2025-11-25' \
  -X POST "https://localhost:8443/mcp/u/$TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 200 — valid origin + valid token
curl -sk -X POST "https://localhost:8443/mcp/u/$TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'Origin: https://pro.openbb.co' -H 'mcp-protocol-version: 2025-11-25' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_connections","arguments":{}}}'
```

## Deploying to a real host

1. Edit `Caddyfile`: replace `localhost:8443 { tls internal ... }` with your domain (Caddy will get a real ACME cert automatically). Change `tls-proxy.ports` in `docker-compose.yml` from `8443:8443` to `443:443`.
2. Update `SNAPTRADE_CONNECTION_REDIRECT` (and the SnapTrade dashboard) to `https://yourhost/widget`.
3. Generate a fresh `SNAPTRADE_STORE_ENCRYPTION_KEY_B64` per environment. Optionally set `SNAPTRADE_AUTH_SECRET` to a separate value if you want token-signature rotation to be independent of credential-encryption rotation, or if you run multiple app replicas behind a load balancer (all replicas must share the same secret to validate each other's tokens).
4. Set `SNAPTRADE_MCP_ALLOW_LOOPBACK=0` in `.env` so the MCP endpoint only accepts `https://*.openbb.<tld>` origins.
5. If you're hosting OpenBB Workspace on a non-`openbb.*` domain, edit [`openbb_snaptrade/mcp_server.py`](openbb_snaptrade/mcp_server.py):
   - Add the hostname to `TransportSecuritySettings.allowed_hosts` and `allowed_origins`.
   - Either extend the `_OPENBB_HOST_RE` regex or replace `_origin_is_allowed()` with an explicit allowlist of your Workspace origins.
6. Make sure Caddy (or your reverse proxy) forwards `Origin`, `Host`, `X-Forwarded-Proto`, and `X-Forwarded-Host` to the app — the absolute-URL rewriting in `/widgets.json` and the MCP origin check depend on them. The bundled `Caddyfile` already does this; if you swap in nginx or ALB, replicate the headers.
7. Put your own SSO / VPN / IP allowlist in front of the whole service if your Workspace instance is multi-tenant. The MCP origin gate stops random cross-site callers but is not a substitute for authenticating *who* is on the other side of an allowed Origin.

The per-user session expires 15 minutes after the last MCP call or `/widgets.json` refresh. While the user is actively using the dashboard the TTL keeps sliding forward; the moment they leave, the agent loses access on its next call.
