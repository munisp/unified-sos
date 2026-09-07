# Edge Architecture: Caddy as the Platform Front Door

## Value analysis

The Unified SOS platform serves 37 state tenants, each with its own citizen
domain (`sos.<state>state.gov.ng`, plus FCT's `sos.fct.gov.ng`) defined in
`config/states/*/branding.json` (`custom_domain`). Caddy sits as the outermost
edge of the platform and owns four concerns:

1. **Edge TLS for all state domains.** Automatic HTTPS with per-domain
   certificates, issued and renewed without operator intervention. For
   additional custom domains not in the static list, **on-demand TLS** issues
   certs at first handshake, gated by an `ask` endpoint so we never issue for
   domains we do not own.
2. **Domain tenancy.** One generated host block per state domain; the Host
   header is preserved end-to-end so downstream routing stays tenant-aware.
3. **Static frontends.** Root traffic for each state domain proxies to the
   citizen PWA static server (`citizen-pwa:3000`); only `/api/*` flows to the
   API gateway.
4. **WAF-lite headers.** HSTS, CSP, `X-Content-Type-Options`, `X-Frame-Options`,
   `Referrer-Policy`, and suppression of the `Server` header are applied
   uniformly at the edge for every tenant.

## Request path

```
citizen ──HTTPS (HTTP/2 + HTTP/3)──▶ Caddy (edge)
                                      ├─ /api/* ──▶ APISIX :9080 ──▶ services
                                      │              (rate limits, routing,
                                      │               OpenAppSec WAF plugin,
                                      │               Keycloak OIDC)
                                      └─ *      ──▶ citizen-pwa :3000 (static)
```

- **APISIX remains the API gateway**: rate limiting, service routing, and the
  OpenAppSec WAF plugin all stay there. Caddy deliberately does *not* duplicate
  deep L7 inspection; it passes `/api/*` through with the tenant Host header
  intact (`header_up Host {host}`).
- **Keycloak OIDC remains at the APISIX/service layer.** Caddy forwards
  `Authorization` and cookie headers untouched (default `reverse_proxy`
  behaviour), so token validation and authn/z never change. Optionally, Caddy's
  `forward_auth` directive can front the PWA for session-gating static assets;
  this is *not* enabled by default because authentication policy belongs to
  Keycloak at the gateway/service layer.

## On-demand TLS and the allowed-domains gate

Caddy's on-demand TLS requires an HTTP GET `ask` endpoint that returns 2xx only
for domains we are allowed to certify. The control-plane registry endpoint
(`POST /cp/v1/domains/verify`, see `services/control-plane/app/branding.py`)
is POST, so we use a **sidecar-file approach** as the primary mechanism:

- `deploy/caddy/generate_caddyfile.py` emits `deploy/caddy/allowed_domains.json`
  from the 37 `config/states/*/branding.json` files.
- A small shim sidecar (added by the orchestrator) serves
  `GET http://control-plane:8000/cp/v1/domains/allowed?domain={domain}` and
  answers 200 only if the domain is present in `allowed_domains.json` (the shim
  may also delegate to the control-plane's POST verify endpoint; the static
  file keeps the gate available even when the control-plane is down).
- The generated Caddyfile configures `on_demand_tls { ask ... interval 2m; burst 5 }`
  to rate-limit issuance against CA quotas.

The 37 known state domains are listed as explicit site blocks, so their
certificates are managed deterministically without invoking the ask path.

## Why Caddy over nginx or Traefik here

- **On-demand TLS for 37+ state domains** is first-class in Caddy (`ask`
  gating + rate limits). nginx has no equivalent; Traefik's on-demand story is
  weaker and its config model is heavier for static multi-tenant host lists.
- **Config simplicity**: the entire edge is one generated Caddyfile
  (~40 lines per tenant) with deterministic drift-checking (`--check`).
- **Automatic certificate renewal** with no cert-manager or cron sidecars.
- **Small footprint**: single static binary, `caddy:2-alpine` base, trivially
  containerized; HTTP/3 (QUIC) and zstd out of the box.

## Certificate storage

Caddy stores certificates and on-demand TLS state under `/data`. Mount a
persistent volume at `/data` (and `/config`) per edge replica. For
multi-replica deployments, use a shared storage backend (Caddy storage
plugin such as Redis/consul) or a per-tenant sharded replica set so each
replica only serves the tenants whose certs it holds; never run replicas with
ephemeral `/data` behind a CA rate limit.

## East-west mTLS (option)

Edge-to-APISIX traffic is plain HTTP on the cluster network by default. For
zero-trust east-west, Caddy can present a client certificate to APISIX
(`reverse_proxy ... { tls client_auth <cert> <key> }` on the upstream transport)
and APISIX verifies against the platform CA. Enable per environment; cert
issuance is owned by the platform PKI, not by this directory.

## Performance notes

- **HTTP/2 and HTTP/3 (QUIC)** are enabled by default on all HTTPS blocks.
- **zstd + gzip** compression (`encode zstd gzip`) for static assets and API
  responses.
- Per-site **JSON access logs** feed the observability stack.
- **Metrics**: the global block enables `servers { metrics }`; Prometheus
  scrapes `:2019/metrics` (admin API bound to `localhost:2019`; the
  orchestrator exposes the scrape path and may disable the admin API entirely).
- One Caddy process comfortably terminates tens of thousands of concurrent
  TLS connections; horizontal scaling is by replicas (see cert storage above).

## Generation and drift control

```
python3 deploy/caddy/generate_caddyfile.py          # regenerate Caddyfile + allowed_domains.json
python3 deploy/caddy/generate_caddyfile.py --check  # CI: fail if committed files drift from config/states
pytest deploy/caddy/test_generate_caddyfile.py      # generator tests
```

The generator is pure stdlib, sorts tenants deterministically, and skips
malformed `custom_domain` values (logged to stderr) so a bad branding file can
never produce an invalid edge config.
