FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Everything that must OUTLIVE the container lives on the volume mounted at
# /data (see docker-compose / Render disk). All three matter and each was
# defaulting inside the image, where a restart silently threw it away:
#   KALSHI_DB      recorded market history
#   PREDLOG_DB     the prediction log -- the accruing track record, and the only
#                  way the model's calibration is ever fitted
#   DEEP_CACHE_DIR the deep-sim cache AND its day-over-day run history, which is
#                  the one thing here that cannot be recomputed after the fact
# A non-root user owns it so the container doesn't run as root.
ENV PORT=8080 KALSHI_DB=/data/markets.db PREDLOG_DB=/data/predlog.db \
    DEEP_CACHE_DIR=/data/deep
RUN mkdir -p /data && useradd -m -u 10001 vigil && chown -R vigil /app /data
USER vigil

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/healthz')" || exit 1

# Single worker (so the background recorders run once), threads for concurrency.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:$PORT app:app"]
