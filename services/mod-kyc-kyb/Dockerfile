FROM python:3.12-slim

WORKDIR /srv/mod-kyc-kyb

# httpx pin mirrors requirements.txt / constraints-live.txt (live registry clients).
RUN pip install --no-cache-dir fastapi==0.116.1 uvicorn[standard]==0.34.0 pydantic==2.11.4 httpx==0.28.1

COPY app ./app

ENV KYC_KYB_MODE=production
EXPOSE 8090

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8090"]
