# syntax=docker/dockerfile:1
# sos-mod-gis-lands — SOS cadastral land administration and titling API
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-mod-gis-lands" \
      org.opencontainers.image.description="SOS cadastral land administration and titling API"

RUN addgroup --system sos && adduser --system --ingroup sos sos

WORKDIR /srv/app

RUN pip install --no-cache-dir fastapi>=0.110 pydantic>=2.6 shapely>=2.0 pyproj>=3.6 cryptography>=42.0 httpx>=0.27 uvicorn[standard]>=0.29

COPY --chown=sos:sos lands_app/ ./lands_app/

USER sos

EXPOSE 8000

CMD ["uvicorn", "lands_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
