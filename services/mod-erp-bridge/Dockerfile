FROM python:3.12-slim

WORKDIR /srv/mod-erp-bridge

# httpx pin mirrors requirements.txt / constraints-live.txt (ERPNext client).
RUN pip install --no-cache-dir fastapi==0.116.1 uvicorn[standard]==0.34.0 pydantic==2.11.4 httpx==0.28.1 PyYAML==6.0.2

COPY app ./app

ENV ERP_BACKEND=local
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
