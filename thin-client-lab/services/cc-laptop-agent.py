#!/usr/bin/env python3
"""Reference laptop agent: what windows/CC-Shell.ps1 does on the agent's Windows
laptop, written for Linux so the simulated lab can test the same protocol.

Runs as the logged-in AD user (who already has a Kerberos ticket):
  1. gets a one-time password from cc-broker with Kerberos,
  2. opens the remote desktop with it (the agent never types it),
  3. waits for "open on my laptop" commands and starts the local app,
  4. when the remote desktop closes, ends (on Windows: signs the user out).
Without --rdp it only listens for commands (used by `lab.sh watch-laptop`).
"""
import argparse
import base64
import json
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

import gssapi


def negotiate_header(spn):
    ctx = gssapi.SecurityContext(name=gssapi.Name(spn, gssapi.NameType.hostbased_service), usage="initiate")
    return "Negotiate " + base64.b64encode(ctx.step()).decode()


def call(args, method, path, timeout=10):
    req = urllib.request.Request(args.broker + path, method=method,
                                 headers={"Authorization": negotiate_header(args.spn)})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", required=True, help="http://broker-01.example.local:8088")
    ap.add_argument("--spn", required=True, help="HTTP@broker-01.example.local")
    ap.add_argument("--rdp", help="RDP command; {user} and {password} are filled in")
    ap.add_argument("--app", action="append", default=[], metavar="ID=COMMAND",
                    help="local program for an app id, e.g. genesys='/opt/genesys/genesys'")
    ap.add_argument("--ticket-only", action="store_true", help="print a one-time ticket and exit")
    args = ap.parse_args()
    apps = dict(a.split("=", 1) for a in args.app)

    try:
        ticket = call(args, "POST", "/sso/ticket")
    except urllib.error.HTTPError as e:
        print(f"no ticket: HTTP {e.code} {e.read().decode(errors='replace')}", flush=True)
        sys.exit(2)
    if args.ticket_only:
        print(json.dumps(ticket))
        return
    if args.rdp:
        rdp_cmd = args.rdp.format(user=shlex.quote(ticket["username"]), password=shlex.quote(ticket["password"]))
        rdp = subprocess.Popen(rdp_cmd, shell=True)
        print(f"remote desktop opened for {ticket['username']}", flush=True)
    else:
        rdp = None   # listen only: the desktop is opened some other way (demo, troubleshooting)
        print(f"listening for {ticket['username']}'s laptop commands (Ctrl+C to stop)", flush=True)

    running = {}
    while rdp is None or rdp.poll() is None:
        try:
            msg = call(args, "GET", "/launch/wait?timeout=20", timeout=30)
        except (urllib.error.URLError, OSError) as e:
            print(f"broker unreachable: {e}", flush=True)
            time.sleep(5)
            continue
        for app in msg.get("apps", []):
            if app not in apps:
                print(f"ignoring unknown app {app!r}", flush=True)
                continue
            proc = running.get(app)
            if proc and proc.poll() is None:
                print(f"{app} already running: bring to front", flush=True)
                subprocess.run(apps[app] + " --focus", shell=True)
            else:
                print(f"starting {app}", flush=True)
                running[app] = subprocess.Popen(apps[app], shell=True)
    print("remote desktop closed: sign out", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
