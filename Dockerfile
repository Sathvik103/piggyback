# Piggyback API + dashboard.  docker compose up  (see docker-compose.yml)
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

RUN useradd --system --uid 10001 piggyback
USER piggyback

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
