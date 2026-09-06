# syntax=docker/dockerfile:1
# sos-edge-daemon — offline-first edge synchronizer (Ed25519-signed outbox,
# store-and-forward sync). Serves the sync gateway endpoint via uvicorn.
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-edge-daemon" \
      org.opencontainers.image.description="SOS edge daemon — offline-first signed outbox and store-and-forward sync for field POS/edge devices"

RUN addgroup --system sos && adduser --system --ingroup sos sos \
    && mkdir -p /var/lib/sos-edge && chown sos:sos /var/lib/sos-edge

WORKDIR /srv/app

RUN pip install --no-cache-dir \
    "fastapi>=0.110" \
    "pydantic>=2.6" \
    "cryptography>=42.0" \
    "httpx>=0.27" \
    "uvicorn[standard]>=0.29"

COPY --chown=sos:sos edge_daemon/ ./edge_daemon/

USER sos

# Local SQLite outbox lives on this volume so pending records survive restarts.
VOLUME ["/var/lib/sos-edge"]

EXPOSE 8000

CMD ["uvicorn", "edge_daemon.gateway:create_gateway_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
