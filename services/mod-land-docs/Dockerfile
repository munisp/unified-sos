# syntax=docker/dockerfile:1
# sos-mod-land-docs — SOS land document management + OCR service (FastAPI reference module)
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-mod-land-docs" \
      org.opencontainers.image.description="SOS mod-land-docs service (land document management, intelligent OCR, versioning, duplicate detection, hash-chained audit)"

RUN addgroup --system sos && adduser --system --ingroup sos sos

WORKDIR /srv/app

RUN pip install --no-cache-dir fastapi>=0.110 pydantic>=2.6 uvicorn[standard]>=0.29 httpx>=0.27 prometheus-client>=0.20

COPY --chown=sos:sos landdocs_app/ ./landdocs_app/

USER sos

EXPOSE 8000

CMD ["uvicorn", "landdocs_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
