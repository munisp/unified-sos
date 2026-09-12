# syntax=docker/dockerfile:1
# sos-mod-mortgage — SOS mortgage & lien management service (FastAPI reference module)
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-mod-mortgage" \
      org.opencontainers.image.description="SOS mod-mortgage service (mortgage & lien management for state land registries)"

RUN addgroup --system sos && adduser --system --ingroup sos sos

WORKDIR /srv/app

RUN pip install --no-cache-dir fastapi>=0.110 pydantic>=2.6 uvicorn[standard]>=0.29

COPY --chown=sos:sos mortgage_app/ ./mortgage_app/

USER sos

EXPOSE 8000

CMD ["uvicorn", "mortgage_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
