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

# TWO worker processes, not one. A single worker meant the platform's health
# check shared a GIL with whatever the app was computing: a DFS optimize or a
# contest sim pegs the CPU in pure Python, the static-200 probe can't get a
# slice, it times out at five seconds and the instance is restarted as "failed"
# while it was merely busy. Separate processes don't share a GIL, so the probe
# always lands somewhere responsive. Background jobs are claimed by one worker
# via a file lock (see app._own_background_jobs), so they still run exactly once.
#
# --max-requests recycles a worker periodically: simulating a slate leaves the
# process permanently ~140 MB fatter (fragmented pymalloc arenas that no
# collection returns), so a long-lived worker only ever grows. Recycling hands
# that memory back. The jitter keeps the two workers from recycling together.
# The worker count is FLOORED AT TWO on purpose. WEB_CONCURRENCY is a name the
# hosting platform may set itself, and if it ever arrives as 1 we would silently
# be back to a single worker -- the exact configuration whose killed worker takes
# the health check, and the whole instance, down with it. VIGIL_WEB_WORKERS wins
# when set; otherwise WEB_CONCURRENCY; otherwise 3. Never fewer than 2.
ENV WEB_CONCURRENCY=3
CMD ["sh", "-c", "W=${VIGIL_WEB_WORKERS:-${WEB_CONCURRENCY:-3}}; \
     case \"$W\" in ''|*[!0-9]*) W=3 ;; esac; [ \"$W\" -lt 2 ] && W=2; \
     echo \"[vigil] starting $W gunicorn workers\"; \
     exec gunicorn -w \"$W\" --threads 8 --timeout 120 --graceful-timeout 30 \
     --max-requests 600 --max-requests-jitter 120 -b 0.0.0.0:$PORT app:app"]
