# Edge Caddy image for the Unified SOS platform.
#
# Build context: deploy/caddy/
#   docker build -t unified-sos/edge-caddy deploy/caddy/
#
# The Caddyfile and allowed_domains.json are GENERATED artifacts. Regenerate
# before building whenever config/states/*/branding.json changes:
#   python3 deploy/caddy/generate_caddyfile.py
FROM caddy:2-alpine

# Generated configuration (37 per-state host blocks + on-demand-TLS fallback).
COPY Caddyfile /etc/caddy/Caddyfile
COPY allowed_domains.json /etc/caddy/allowed_domains.json

# Non-root notes:
#   * The upstream caddy image runs as root by default so it can bind :80/:443.
#     In Kubernetes, keep the container rootless by binding only high ports
#     (e.g. 8080/8443 via `http_port`/`https_port` or a compose/helm port map),
#     set `user: "1000:1000"` in the orchestrator, and mount the cert volumes
#     (/data, /config) with fsGroup so the caddy user can write certificates.
#   * The admin API is bound to localhost:2019 (see Caddyfile global block);
#     Prometheus scrapes :2019/metrics via a container localhost probe or an
#     orchestrator-exposed scrape sidecar port.
#   * /data (certificates + on-demand TLS state) must be a persistent
#     per-replica/per-tenant volume -- see docs/architecture/edge-caddy.md.

EXPOSE 80 443 443/udp 2019

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
	CMD wget -qO- http://localhost:2019/config/ >/dev/null 2>&1 || exit 1

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
