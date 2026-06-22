FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite lives on a persistent volume mounted at /data (see docker-compose /
# Render disk). A non-root user owns it so the container doesn't run as root.
ENV PORT=8080 KALSHI_DB=/data/markets.db
RUN mkdir -p /data && useradd -m -u 10001 vigil && chown -R vigil /app /data
USER vigil

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz')" || exit 1

# Single worker (so the background recorders run once), threads for concurrency.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:$PORT app:app"]
