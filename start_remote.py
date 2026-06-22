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
