# syntax=docker/dockerfile:1
# sos-control-plane — SOS global control plane — tenant provisioning, policy-pack registry, audit events
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-control-plane" \
      org.opencontainers.image.description="SOS global control plane — tenant provisioning, policy-pack registry, audit events"

RUN addgroup --system sos && adduser --system --ingroup sos sos

WORKDIR /srv/app

RUN pip install --no-cache-dir fastapi>=0.110 pydantic>=2.6 jsonschema>=4.21 uvicorn[standard]>=0.29

COPY --chown=sos:sos app/ ./app/

USER sos

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
