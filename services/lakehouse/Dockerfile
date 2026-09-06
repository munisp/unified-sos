# syntax=docker/dockerfile:1
# sos-lakehouse — batch medallion pipeline (bronze/silver/gold as pure functions).
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.source="https://github.com/munisp/unified-sos" \
      org.opencontainers.image.title="sos-lakehouse" \
      org.opencontainers.image.description="SOS lakehouse batch job — medallion pipeline (pandas/pyarrow) producing bronze/silver/gold Parquet layers"

RUN addgroup --system sos && adduser --system --ingroup sos sos

WORKDIR /srv/job

RUN pip install --no-cache-dir "pandas>=2.1" "pyarrow>=15"

COPY --chown=sos:sos lakehouse/ ./lakehouse/

USER sos

# Batch entrypoint: the medallion module is a library of pure pipeline
# functions (ingest_raw_events -> normalize_events -> daily_igr_by_state ->
# write_parquet); the default CMD runs a bronze->silver->gold pass over
# /data/raw into /data/lake. Override the command for ad-hoc jobs.
ENTRYPOINT ["python", "-c", "import sys; from pathlib import Path; from lakehouse import medallion as m; raw=Path(sys.argv[1]); out=Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True); m.write_parquet(m.daily_igr_by_state(m.normalize_events(m.ingest_raw_events(raw))), out/'gold_daily_igr.parquet')"]
CMD ["/data/raw", "/data/lake"]
