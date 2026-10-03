# syntax=docker/dockerfile:1
# sos-mod-mobility-switch — SOS mod-mobility-switch service (FastAPI reference module)
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-mod-mobility-switch" \
      org.opencontainers.image.description="SOS mod-mobility-switch service (FastAPI reference module)"

RUN addgroup --system sos && adduser --system --ingroup sos sos

WORKDIR /srv/app

# tigerbeetle==0.17.9: official TigerBeetle Python client, pinned in
# pyproject.toml; the production settlement ledger (SOS_MOBILITY_LEDGER_URL)
# lazy-imports it and fails closed without it. Client release must match the
# TigerBeetle cluster release. (Specs quoted so `>=` is not a shell redirect.)
RUN pip install --no-cache-dir "fastapi>=0.110" "pydantic>=2.6" "uvicorn[standard]>=0.29" "tigerbeetle==0.17.9"

COPY --chown=sos:sos app/ ./app/

USER sos

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
