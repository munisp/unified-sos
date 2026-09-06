# syntax=docker/dockerfile:1
# sos-mod-gis-luc — SOS Land Use Charge valuation and billing API
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-mod-gis-luc" \
      org.opencontainers.image.description="SOS Land Use Charge valuation and billing API"

RUN addgroup --system sos && adduser --system --ingroup sos sos

WORKDIR /srv/app

RUN pip install --no-cache-dir fastapi>=0.110 pydantic>=2.6 httpx>=0.27 uvicorn[standard]>=0.29

COPY --chown=sos:sos luc_app/ ./luc_app/

USER sos

EXPOSE 8000

CMD ["uvicorn", "luc_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
