# Deploying Vigil

Goal: get Vigil off your PC and onto a real, always-on, HTTPS website. Pick one
of the paths below. All three give you a `https://…` URL with a valid TLS
certificate; they differ in cost, effort, and how "your own server" it feels.

| Path | Cost | Effort | Always-on | Persistent data | Deep sim finishes? |
|------|------|--------|-----------|-----------------|--------------------|
| **Render (free)** | $0 | clicks | ❌ sleeps when idle | ❌ resets on deploy | ❌ no disk |
| **Render (Starter)** | ~$7/mo | clicks | ✅ | ✅ (1 GB disk) | ✅ ~1 worker, overnight |
| **Render (Standard)** | ~$25/mo | clicks | ✅ | ✅ (1 GB disk) | ✅ more headroom, 2 GB |
| **VPS + Docker + Caddy** | ~$5–12/mo | some CLI | ✅ | ✅ (volume) | ✅ faster, 2–4 workers |

All paid plans are a FLAT monthly fee, not metered by traffic or uptime: an
always-on service that is busy costs exactly what an idle one costs. Memory,
not billing, is what limits how much the box can do at once.

### Staying logged in

`APP_PASSWORD` uses HTTP Basic auth, which phones forget constantly — the
browser drops cached credentials whenever it evicts the tab. So a successful
login also sets a signed cookie (`vigil_auth`) and that carries you afterwards.

| Setting | Default | What it does |
|---|---|---|
| `VIGIL_REMEMBER_DAYS` | `90` | How long a device stays logged in. |
| `VIGIL_TRUSTED_IPS` | unset | Comma-separated IPs/CIDRs that skip the password entirely. |

The cookie is an HMAC over its own expiry, keyed by `SECRET_KEY` — there is no
password in it and nothing to forge. It is HttpOnly, Secure over HTTPS, and
SameSite=Lax. **Rotating `SECRET_KEY` signs every device out**, which is the
revoke button if a phone is lost; `/logout` signs out just the current device.

Set `SECRET_KEY`. Without it the signing key is derived from the password so
that all workers still agree — but then changing the password also signs
everyone out, and you lose the ability to revoke without changing it.

**On `VIGIL_TRUSTED_IPS`:** reasonable for a home connection with a stable
address, and a bad idea for a phone. Mobile IPs rotate and sit behind
carrier-grade NAT, so an allowlist would both break often and admit everyone
sharing that carrier pool. Use the cookie for the phone.

**Only enable the allowlist behind a real proxy** (Render or Caddy — both
paths here). The client IP is read via `X-Forwarded-For`, which those proxies
set. An app exposed directly to the internet can be sent a forged
`X-Forwarded-For: <your-ip>` and the allowlist would wave it through. The
cookie has no such weakness.

### Never run one web worker

`WEB_CONCURRENCY` must be ≥ 2 on any always-on deploy. When a request outlives
gunicorn's `--timeout` the arbiter kills the worker serving it; with a single
worker there is then nothing alive to answer `/healthz`, so the platform's probe
times out and it restarts an instance that was only ever busy — surfacing as
"HTTP health check failed (timed out after 5 seconds)". Measured on one CPU with
every thread tied up in long builds: **1 worker failed 7/7 probes, 2 workers
failed 0/89.** The recorders, the prediction grader and the nightly sims stay
singular regardless, claimed by one worker through a file lock
(`app._own_background_jobs`), so extra workers duplicate no background work.

### Sizing the box — measured

The deep season sim decides your plan. The numbers below are **peak PSS**, which
is what a container's memory limit actually enforces; summed RSS looks several
times larger because it double-counts the copy-on-write pages forked workers
share, and reading RSS is how you talk yourself into buying a bigger box than
you need.

| | peak PSS | 4,000 seasons |
|---|---|---|
| app idle | 35 MB | — |
| **+ sim, 1 worker** | **336 MB** | ~0.9 h |
| + sim, 2 workers | 613 MB | ~0.5 h |
| + sim, 4 workers | 562 MB | ~0.3 h |

So **512 MB is enough at one worker**, which is why Starter works if you are
happy for the run to take the night — and at midnight, you probably are.

**The catch, and it is handled for you:** `multiprocessing.cpu_count()` reports
the HOST's cores, not your container's quota. Left alone it would start eight
workers on half a core against a 512 MB cap and get the sim OOM-killed — while
the web app survives, so it looks like the run merely never finishes. The app now
reads the real cgroup CPU and memory limits and sizes the pool to whichever binds
first. Override with `VIGIL_SIM_WORKERS` if you want to force it.

> To cut the nightly cost by roughly two thirds, set `VIGIL_MAX_ATTRIB=0`. You
> keep the calendar and every "what changed" sentence and drop only the measured
> pp figures — which, as the attribution notes explain, are frequently "no
> measurable effect" anyway.

> Security note: a **managed host (Render)** is usually the *safer* default —
> they patch the OS, terminate TLS, and isolate your app, so there's less for
> you to secure. A VPS gives you full control but you own the hardening.

The app already ships production-ready: runs under gunicorn, sets security
headers (HSTS/nosniff/anti-clickjacking), trusts the platform's HTTPS proxy,
exposes `/healthz`, blocks search indexing via `/robots.txt`, and can require an
HTTP login via `APP_PASSWORD`.

---

## Environment variables

| Var | What it does | Recommended |
|-----|--------------|-------------|
| `SECRET_KEY` | Flask signing key | a long random string (`python -c "import secrets;print(secrets.token_hex(32))"`) |
| `APP_PASSWORD` | if set, the whole site requires HTTP login | set one while it's private; blank = open |
| `APP_USER` | username for that login | `vigil` (default) |
| `KALSHI_DB` | recorded market history | `/data/markets.db` on a persistent disk |
| `PREDLOG_DB` | prediction log (your track record; calibration is fitted from it) | `/data/predlog.db` |
| `DEEP_CACHE_DIR` | deep-sim cache **and its run history** | `/data/deep` |
| `VIGIL_SIM_WORKERS` | force the sim's pool size | leave unset — auto-sized from the container's limits |
| `VIGIL_MAX_ATTRIB` | how many changes get a measured pp figure | `6`; set `0` to skip pricing and cut the night by ~2/3 |
| `TIERS_ENFORCED` | turn on subscription gating | `0` (off — owner/God mode) for now |
| `PORT` | port to bind | set by the host automatically |

---

### The SIM_TOKEN pair (workflow authentication)

The two scheduled workflows — the nightly sim-history snapshot and the
6-hourly error-log snapshot — fetch data from the app. Once `APP_PASSWORD` is
set they need credentials, and they authenticate with an `X-Sim-Token` header.
Setup is one value in two places:

1. Generate a long random string
   (`python -c "import secrets;print(secrets.token_hex(24))"`).
2. Render dashboard → your service → **Environment** → add `SIM_TOKEN` = that
   value.
3. GitHub repo → **Settings → Secrets and variables → Actions → Secrets** →
   add `SIM_TOKEN` = the same value.

Deliberately NOT declared in `render.yaml`: a Blueprint update that introduces
a new `sync: false` variable stalls the deploy waiting for its value in the
dashboard — which failed a deploy for real. Adding it by hand skips that.
Without the pair, the workflows fail their fetch step with an explanatory log
line (not silently) and commit nothing.

## Path A — Render (easiest)

1. Push this repo to GitHub (already your setup).
2. On <https://render.com> → **New → Blueprint**, pick the repo. It reads
   `render.yaml` and creates the service (Docker, health check, headers).
3. In the service's **Environment**, set `APP_PASSWORD` to something only you
   know (keeps it private during build-out). `SECRET_KEY` is auto-generated.
4. Deploy. You get `https://vigil-xxxx.onrender.com`. Add a custom domain under
   **Settings → Custom Domains** (free TLS).

**To make it run 24/7 and keep its data:** change the plan to **Starter**, then
in `render.yaml` uncomment the `disk:` block **and all three env vars** —
`KALSHI_DB`, `PREDLOG_DB`, `DEEP_CACHE_DIR` — and redeploy. Missing any one of
them leaves that store inside the container, where every restart wipes it.
(Free services sleep when idle and have no disk, so logging pauses and the data
resets on each deploy — fine for a demo, not for accumulating a track record.)

---

## Path B — Your own server (VPS + Docker + Caddy)

Worth it if you want the sim to finish in half an hour rather than overnight, or
you would rather not be on a managed platform. A 2 vCPU / 4 GB box (Hetzner CX22,
DigitalOcean 2 GB, Vultr equivalent — roughly $5–12/mo) runs it with 2–4 workers.
This puts the app behind **Caddy**, which fetches and auto-renews a Let's Encrypt
certificate.

1. Create the VPS (Ubuntu), point your domain's **A record** at its IP.
2. Install Docker: `curl -fsSL https://get.docker.com | sh`
3. Clone the repo, then:
   ```bash
   cp .env.example .env
   # edit .env: set DOMAIN, generate SECRET_KEY, set APP_PASSWORD
   docker compose up -d --build
   ```
4. Visit `https://your-domain` — Caddy issues the cert automatically.

Data lives in the `vigil-data` Docker volume and survives restarts/redeploys.
Update later with `git pull && docker compose up -d --build`.

**Basic VPS hardening:** use SSH keys (disable password login), enable a
firewall allowing only 22/80/443 (`ufw allow OpenSSH; ufw allow 80; ufw allow
443; ufw enable`), and keep `APP_PASSWORD` set until you're ready to go public.

---

## Path C — any other host (Railway, Fly.io, Heroku-likes)

The repo also has a `Procfile` and `Dockerfile`, so most platforms work:
point them at the Dockerfile (preferred) or the `Procfile`
(`web: gunicorn -w 1 --threads 8 …`). Set the env vars above and mount a volume
at `/data` for persistence.

---

## After it's live
- Confirm `https://…/healthz` returns `ok`.
- Confirm the lock icon (valid TLS).
- If you set `APP_PASSWORD`, confirm the browser prompts for login.
- Keep one web worker (`-w 1`) so the background recorders run a single copy.

---

## Keeping the deep-sim history (the "What happened" calendar)

Everything else the app stores can be recomputed. The **run history cannot** —
once a nightly run overwrites the previous one, yesterday's rosters and yesterday's
odds are gone. On a host with no persistent disk (Render free), the cache is wiped
on every restart, redeploy and idle-sleep, so the calendar would quietly empty
itself.

The fix is to keep it in the repo, which is the one thing that survives.

**Setup — one variable.** Settings → Secrets and variables → Actions → Variables,
add `APP_URL` = your deployed URL (e.g. `https://vigil.onrender.com`). That is the
same variable the weekly sim workflow already uses; if you have set it, you are
done. Without it the workflow exits quietly instead of failing.

**How it works.** `.github/workflows/nightly-history.yml` runs daily (and has a
"Run workflow" button for when you want it saved right now). Each run:

1. pulls `/api/baseball/futures/deep/history/export` from the app,
2. commits it to the **`sim-history`** branch under `history/mlb/`,
3. *then* triggers the next deep run — so the day being replaced is banked first.

The app restores from that branch automatically on boot, so a restart comes back
with its calendar intact.

**Three deliberate choices, in case you wonder later:**

- **The app never writes to GitHub and holds no token.** The workflow pulls and
  commits with its own `GITHUB_TOKEN`. Reading back needs no credentials because
  the repo is public.
- **It commits to `sim-history`, not the deploy branch.** Render auto-deploys from
  the default branch, so nightly commits there would redeploy the app every night —
  restarting it, wiping the cache this exists to protect, and possibly killing a
  sim mid-run. `sim-history` is an orphan branch holding data only, no code.
- **Only the newest day keeps its roster fingerprint.** It exists to diff the next
  run against; keeping one per day would commit ~100KB of dead weight nightly,
  forever. A day file is ~12KB, so a full season is a few MB.

**Two things it does not do.** It does not persist the full team profiles used for
attribution (~1.5MB/night is too much to commit), so the first run after a restore
lists what changed but cannot price it — you get the sentences without the pp
figures, and normal service resumes the next night. And on Render free the app
sleeps and is CPU-limited, so a 4,000-season run plus counterfactuals may not
finish; the workflow retries while waking the host, but a consistently sparse
calendar means the host, not the snapshotting.

> The repo is **public**, so anything committed here is publicly visible. The
> history is MLB roster data and sim outputs — nothing personal — and the model
> code is already public, but it is worth knowing before you turn it on.

## PC compute worker (optional, big speedup)

Your desktop simulates the slate and uploads the results; Render adopts
whatever is freshest and computes for itself whenever the PC is off. A game
sim is ~32s on a desktop vs 200s+ on the shared cloud CPU, so with the worker
running, the combo maker's Build is nearly always instant.

One-time setup on the PC (Windows):

1. Install **Python 3.11+** from python.org — tick **"Add python.exe to
   PATH"** during install.
2. Install **Git for Windows** (defaults are fine).
3. Clone the repo: open a folder, right-click → *Open in Terminal* →
   `git clone https://github.com/mmfoodchicks/kalshi-betting-helper.git`
4. In the cloned folder, copy `vigil-pc.cfg.example` to `vigil-pc.cfg` and
   fill in your app URL and the same SIM_TOKEN value used above. This file is
   gitignored — it never leaves the machine.
5. Double-click **vigil-pc.bat**. It pulls the latest code, runs one sim
   cycle, uploads, sleeps 10 minutes, repeats. Close the window to stop —
   Render instantly goes back to computing everything itself.
6. Optional, to make it fully hands-off: Task Scheduler → Create Basic Task →
   trigger *At log on* → action *Start a program* → browse to vigil-pc.bat.

Safety model: uploads require SIM_TOKEN, are schema-versioned (a stale
checkout gets a 409 and simply waits for its next `git pull`), and land
atomically in the same cache the workers already share. The server never
depends on the PC — worst case is always "compute it myself, like before."
