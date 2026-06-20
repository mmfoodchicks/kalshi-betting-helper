"""One command to run the app AND expose it to the internet via a free Cloudflare
tunnel, so you can reach it from your phone anywhere:

    python start_remote.py

It launches the server, starts `cloudflared`, and prints the public HTTPS URL.
Open that URL on your phone and Add to Home Screen. (Tip: set a password first
so the public URL isn't open to anyone --  APP_PASSWORD=yourpass.)

Requires cloudflared once:
    Windows:  winget install --id Cloudflare.cloudflared
    macOS:    brew install cloudflared
    else:     https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
"""

import os
import re
import shutil
import subprocess
import sys
import time

PORT = os.environ.get("PORT", "5000")


def main():
    env = {**os.environ, "PORT": PORT}
    app_proc = subprocess.Popen([sys.executable, "app.py"], env=env)

    if not os.environ.get("APP_PASSWORD"):
        print("\n  ⚠  No APP_PASSWORD set — the public URL will be open to anyone "
              "who has it.\n     Stop, set a password, and re-run to lock it down:")
        print("       Windows:  $env:APP_PASSWORD='yourpass'; python start_remote.py")
        print("       Mac/Lin:  APP_PASSWORD=yourpass python start_remote.py\n")

    cf = shutil.which("cloudflared")
    if not cf:
        print("  cloudflared isn't installed (needed for the public URL). Install it once:")
        print("     Windows:  winget install --id Cloudflare.cloudflared")
        print("     macOS:    brew install cloudflared")
        print("     other:    https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        print(f"\n  The app is still running locally at http://localhost:{PORT}\n")
        try:
            app_proc.wait()
        except KeyboardInterrupt:
            app_proc.terminate()
        return

    time.sleep(2)
    tunnel = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    shown = False
    try:
        for line in tunnel.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not shown:
                shown = True
                url = m.group(0)
                print("\n" + "=" * 64)
                print("  📱  OPEN THIS ON YOUR PHONE  (then Add to Home Screen):")
                print("      " + url)
                print("=" * 64 + "\n")
    except KeyboardInterrupt:
        pass
    finally:
        tunnel.terminate()
        app_proc.terminate()


if __name__ == "__main__":
    main()
