# syntax=docker/dockerfile:1
# sos-mod-geospatial — SOS geospatial orchestration service (datasets, jobs, H3,
# GeoLibre projects, lakehouse publication).
#
# Build context: REPOSITORY ROOT (the service reuses the tested deterministic
# runners in geospatial/local read-only):
#   docker build -f services/mod-geospatial/Dockerfile .
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GEOSPATIAL_MODE=local \
    GEOSPATIAL_LOCAL_DIR=/srv/geospatial

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-mod-geospatial" \
      org.opencontainers.image.description="SOS geospatial orchestration service (datasets, jobs, H3, GeoLibre, lakehouse)"

RUN addgroup --system sos && adduser --system --ingroup sos sos

WORKDIR /srv/app

RUN pip install --no-cache-dir fastapi>=0.110 pydantic>=2.6 shapely>=2.0 numpy>=1.26 httpx>=0.27 uvicorn[standard]>=0.29

COPY --chown=sos:sos services/mod-geospatial/app/ ./app/
COPY --chown=sos:sos geospatial/local/ /srv/geospatial/local/

USER sos

EXPOSE 8013

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8013"]
