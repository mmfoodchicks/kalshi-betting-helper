# Deploying Vigil

Goal: get Vigil off your PC and onto a real, always-on, HTTPS website. Pick one
of the paths below. All three give you a `https://…` URL with a valid TLS
certificate; they differ in cost, effort, and how "your own server" it feels.

| Path | Cost | Effort | Always-on | Persistent data | Deep sim finishes? |
|------|------|--------|-----------|-----------------|--------------------|
| **Render (free)** | $0 | clicks | ❌ sleeps when idle | ❌ resets on deploy | ❌ |
| **Render (Starter)** | ~$7/mo | clicks | ✅ | ✅ (1 GB disk) | ❌ **512 MB — OOM** |
| **Render (Standard)** | ~$25/mo | clicks | ✅ | ✅ | ✅ ~3 h/night |
| **VPS + Docker + Caddy** | ~$5–12/mo | some CLI | ✅ | ✅ (volume) | ✅ ~1–2 h/night |

### Sizing the box — measured, not guessed

The deep season sim is the thing that decides your plan, and it is heavier than
a web app looks:

- **Memory: ~840 MB peak** across its worker processes on four cores. Anything
  with 512 MB — Render **Free and Starter both** — gets the sim OOM-killed. The
  site stays up and the run simply never finishes, which is a confusing way to
  fail. **Starter is not enough, despite being the paid tier.**
- **CPU: ~1.1 core-hours** for a 4,000-season run, plus ~1.9 more if attribution
  is on (see `VIGIL_MAX_ATTRIB`). On half a core that is a six-hour night; on
  2–4 cores it is one to two hours.

So the honest comparison is **Render Standard (~$25/mo) vs a 2–4 core VPS
(~$5–12/mo)**. The VPS is cheaper *and* several times faster for this workload,
at the cost of running a couple of commands yourself. If you would rather never
touch a terminal, Standard is fine — just not Starter.

> If you want to stay small, set `VIGIL_MAX_ATTRIB=0`. You keep the calendar and
> the "what changed" sentences and drop only the measured pp figures — which, as
> the attribution notes explain, are frequently "no measurable effect" anyway.
> That cuts the nightly cost by roughly two thirds.

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
| `KALSHI_DB` | SQLite path | `/data/markets.db` on a persistent disk |
| `TIERS_ENFORCED` | turn on subscription gating | `0` (off — owner/God mode) for now |
| `PORT` | port to bind | set by the host automatically |

---

## Path A — Render (easiest)

1. Push this repo to GitHub (already your setup).
2. On <https://render.com> → **New → Blueprint**, pick the repo. It reads
   `render.yaml` and creates the service (Docker, health check, headers).
3. In the service's **Environment**, set `APP_PASSWORD` to something only you
   know (keeps it private during build-out). `SECRET_KEY` is auto-generated.
4. Deploy. You get `https://vigil-xxxx.onrender.com`. Add a custom domain under
   **Settings → Custom Domains** (free TLS).

**To make it run 24/7 and keep its data:** change the plan to **Standard** (not
Starter — see the sizing note above; 512 MB OOM-kills the deep sim), then in
`render.yaml` uncomment the `disk:` block **and all three env vars** —
`KALSHI_DB`, `PREDLOG_DB`, `DEEP_CACHE_DIR` — and redeploy. Missing any one of
them leaves that store inside the container, where every restart wipes it.
(Free services sleep when idle and have no disk, so logging pauses and the data
resets on each deploy — fine for a demo, not for accumulating a track record.)

---

## Path B — Your own server (VPS + Docker + Caddy)

**Recommended for this app.** Pick a box with **at least 2 vCPU and 4 GB RAM**
(Hetzner CX22, DigitalOcean 2 GB/2 vCPU, Vultr equivalent — roughly $5–12/mo).
That is what the deep sim needs; see the sizing note above. This runs the app
behind **Caddy**, which fetches and auto-renews a Let's Encrypt certificate.

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
