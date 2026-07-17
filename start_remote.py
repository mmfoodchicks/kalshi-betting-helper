"""One command to run the app and get it on your phone FAST:

    python start_remote.py

It starts the server and immediately prints a QR code for your home-wifi URL
(scan it, no install needed). If `cloudflared` is installed it also opens a free
public HTTPS tunnel and prints a second QR so it works ANYWHERE (e.g. at work).
On the phone: scan -> open -> share/menu -> "Add to Home Screen".

Optional: set APP_PASSWORD=yourpass first so a public URL isn't open to anyone.
For the nicest QR: pip install qrcode   (falls back to a plain link otherwise)

cloudflared (one-time, only for the public URL):
    Windows:  winget install --id Cloudflare.cloudflared
    macOS:    brew install cloudflared
    other:    https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import time

PORT = os.environ.get("PORT", "5000")
_ROOT = os.path.dirname(os.path.abspath(__file__))


def _git(*args):
    try:
        return subprocess.run(["git", "-C", _ROOT, *args],
                              capture_output=True, text=True, timeout=60)
    except Exception:
        return None


def _sync_latest():
    """Pull the latest of the CURRENT branch before starting, so 'run
    start_remote.py' always serves the newest code — no more 'I refreshed but
    nothing changed'. Prints the branch + commit so it's obvious what's live.
    Skips quietly if this isn't a git checkout or there's no network."""
    if not os.path.isdir(os.path.join(_ROOT, ".git")):
        return
    br = _git("rev-parse", "--abbrev-ref", "HEAD")
    branch = br.stdout.strip() if br and br.returncode == 0 else None
    if not branch or branch == "HEAD":
        return
    print(f"\n  ⟳ Updating code on branch '{branch}' …")
    fetched = _git("fetch", "--quiet", "origin", branch)
    if fetched and fetched.returncode == 0:
        local = (_git("rev-parse", "HEAD") or _R()).stdout.strip()
        remote = (_git("rev-parse", f"origin/{branch}") or _R()).stdout.strip()
        if remote and local and local != remote:
            # Match the self-updater: hard-reset to origin (DB/caches are
            # gitignored, so only tracked source advances — nothing is lost).
            reset = _git("reset", "--hard", f"origin/{branch}")
            if reset and reset.returncode == 0:
                print("  ✓ Pulled the latest changes.")
            else:
                print("  ⚠ Couldn't fast-forward (local edits?). Running current files.")
        else:
            print("  ✓ Already up to date.")
    else:
        print("  ⚠ Couldn't reach GitHub — running the code already on disk.")
    head = _git("log", "-1", "--format=%h  %s")
    if head and head.returncode == 0:
        print(f"  build: {_build_token()}   commit: {head.stdout.strip()}")
    print(f"  ⚠ NOTE: updates only land if THIS branch ('{branch}') is the one they were pushed to.\n")


class _R:
    """Null result so _sync_latest stays simple when a git call returns None."""
    stdout = ""
    returncode = 1


def _build_token():
    """Same cache-busting token app.py serves and the footer shows, so the
    terminal and the phone agree on which build is live."""
    try:
        sd = os.path.join(_ROOT, "static")
        return str(int(max(os.path.getmtime(os.path.join(sd, f))
                           for f in ("app.js", "style.css", "sw.js"))))
    except Exception:
        return "?"


def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


def _qr(url):
    """Print a scannable QR for the URL if the `qrcode` lib is available."""
    try:
        import qrcode
        q = qrcode.QRCode(border=1)
        q.add_data(url)
        q.make()
        q.print_ascii(invert=True)
        return True
    except Exception:
        return False


def _banner(title, url):
    print("\n" + "=" * 60)
    print("  " + title)
    if not _qr(url):
        print("  (pip install qrcode  for a scannable QR code)")
    print("  " + url)
    print("=" * 60 + "\n")


def main():
    _sync_latest()
    env = {**os.environ, "PORT": PORT}
    app_proc = subprocess.Popen([sys.executable, "app.py", "--port", PORT], env=env)
    time.sleep(2)

    if not os.environ.get("APP_PASSWORD"):
        print("\n  ⚠  No APP_PASSWORD set — a public tunnel URL would be open to anyone.")
        print("     Lock it:  Windows  $env:APP_PASSWORD='pw'; python start_remote.py")
        print("               Mac/Lin  APP_PASSWORD=pw python start_remote.py")

    # Same-wifi URL works instantly with zero install — scan and go.
    ip = _lan_ip()
    if ip:
        _banner("📶  ON HOME WIFI — scan this, then Add to Home Screen:",
                f"http://{ip}:{PORT}")

    cf = shutil.which("cloudflared")
    if not cf:
        print("  For access AWAY from home (e.g. at work), install cloudflared once:")
        print("     Windows:  winget install --id Cloudflare.cloudflared")
        print("     macOS:    brew install cloudflared")
        print(f"\n  Running locally at http://localhost:{PORT}\n")
        try:
            app_proc.wait()
        except KeyboardInterrupt:
            app_proc.terminate()
        return

    tunnel = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    shown = False
    try:
        for line in tunnel.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not shown:
                shown = True
                _banner("🌍  WORKS ANYWHERE — scan this, then Add to Home Screen:",
                        m.group(0))
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
        app_proc.terminate()


if __name__ == "__main__":
    main()
