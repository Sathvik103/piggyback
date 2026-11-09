# Piggyback API + web app.  docker compose --profile app up -d --build

# 1. Build the React app (outputs to api/static).
FROM node:22-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# 2. Python runtime.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY simulator ./simulator
COPY detection ./detection
COPY recovery ./recovery
COPY db ./db
COPY api ./api
COPY benchmark ./benchmark
COPY results ./results
COPY --from=web /src/api/static ./api/static

RUN useradd --system --uid 10001 piggyback
USER piggyback

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
