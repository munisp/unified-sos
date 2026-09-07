# syntax=docker/dockerfile:1
# sos-mod-ml-inference — CPU-only ML inference service (FastAPI + torch CPU)
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SOS_ML_ARTIFACTS_DIR=/srv/artifacts \
    SOS_ML_THREADS=1

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-mod-ml-inference" \
      org.opencontainers.image.description="SOS CPU ML inference (fraud_gnn, credit_mlp, luc_avm, crowd_lstm)"

RUN addgroup --system sos && adduser --system --ingroup sos sos

WORKDIR /srv/app

# CPU-only torch wheel keeps the image small (no CUDA toolchain).
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir fastapi>=0.110 pydantic>=2.6 uvicorn[standard]>=0.29

COPY --chown=sos:sos app/ ./app/
# Optional: copy the training stack's architectures when built from the repo
# root context (`docker build -f services/mod-ml-inference/Dockerfile .`).
# The service fails closed in production without them; the fixture profile
# serves a deterministic heuristic fallback tagged model_version=fixture.

# Mount model artifacts at /srv/artifacts (SOS_ML_ARTIFACTS_DIR).
VOLUME ["/srv/artifacts"]

USER sos

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
