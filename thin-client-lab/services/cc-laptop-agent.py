#!/usr/bin/env python3
"""Reference laptop agent: what windows/CC-Shell.ps1 does on the agent's Windows
laptop, written for Linux so the simulated lab can test the same protocol.

Runs as the logged-in AD user (who already has a Kerberos ticket):
  1. gets a one-time password from cc-broker with Kerberos,
  2. opens the remote desktop with it (the agent never types it),
  3. waits for "open on my laptop" commands and starts the local app,
  4. with --console: reports to cc-console (health, events) and carries out the
     admins' actions (view screen, message, start app, reconnect, sign out),
  5. when the remote desktop closes, ends (on Windows: signs the user out).
Without --rdp it only listens for commands (used by `lab.sh watch-laptop`).
"""
import argparse
import base64
import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import gssapi


def negotiate_header(spn):
    ctx = gssapi.SecurityContext(name=gssapi.Name(spn, gssapi.NameType.hostbased_service), usage="initiate")
    return "Negotiate " + base64.b64encode(ctx.step()).decode()


def call(base, spn, method, path, payload=None, timeout=10):
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Authorization": negotiate_header(spn), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


class Laptop:
    def __init__(self, args):
        self.args = args
        self.apps = dict(a.split("=", 1) for a in args.app)
        self.running = {}
        self.rdp = None
        self.reconnect = False
        self.signing_out = False
        self.broker_ok = True
        self.events, self.lock = [], threading.Lock()
        self._cpu = None

    def cpu_percent(self):
        vals = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        idle, total = vals[3] + vals[4], sum(vals)
        pct = None
        if self._cpu and total != self._cpu[1]:
            pct = round(100 * (1 - (idle - self._cpu[0]) / (total - self._cpu[1])), 1)
        self._cpu = (idle, total)
        return pct

    def event(self, kind, message):
        print(message, flush=True)
        with self.lock:
            self.events.append({"ts": time.time(), "kind": kind, "message": message})

    def broker(self, method, path, timeout=10):
        return call(self.args.broker, self.args.spn, method, path, timeout=timeout)

    # ---- the remote desktop -------------------------------------------
    def open_desktop(self):
        ticket = self.broker("POST", "/sso/ticket")
        cmd = self.args.rdp.format(user=shlex.quote(ticket["username"]), password=shlex.quote(ticket["password"]))
        self.rdp = subprocess.Popen(cmd, shell=True)
        self.event("desktop", f"remote desktop opened for {ticket['username']}")

    def desktop_open(self):
        return self.rdp is not None and self.rdp.poll() is None

    def close_desktop(self):
        subprocess.run(["pkill", "-u", str(os.getuid()), "-x", "xfreerdp3"])

    # ---- local apps (Genesys) ------------------------------------------
    def launch(self, app):
        if app not in self.apps:
            self.event("app", f"ignoring unknown app {app!r}")
            return False
        proc = self.running.get(app)
        if proc and proc.poll() is None:
            self.event("app", f"{app} already running: bring to front")
            subprocess.run(self.apps[app] + " --focus", shell=True)
        else:
            self.event("app", f"starting {app}")
            self.running[app] = subprocess.Popen(self.apps[app], shell=True)
        return True

    def app_running(self, app):
        p = self.running.get(app)
        return p is not None and p.poll() is None

    # ---- what the laptop screen shows -----------------------------------
    def screenshot(self):
        out = subprocess.run(["ps", "-u", str(os.getuid()), "-o", "args="], capture_output=True, text=True).stdout
        xvfb = next((l.split() for l in out.splitlines() if l.startswith("Xvfb ")), None)
        if not xvfb:
            raise RuntimeError("no screen (remote desktop not open)")
        display = xvfb[1]
        auth = xvfb[xvfb.index("-auth") + 1] if "-auth" in xvfb else ""
        env = dict(os.environ, DISPLAY=display, XAUTHORITY=auth)
        xwd = subprocess.run(["xwd", "-root", "-silent"], capture_output=True, env=env, timeout=20)
        png = subprocess.run(["convert", "xwd:-", "-resize", "1280x>", "png:-"], input=xwd.stdout,
                             capture_output=True, timeout=20)
        if png.returncode != 0:
            raise RuntimeError(png.stderr.decode(errors="ignore")[-300:])
        return png.stdout

    # ---- cc-console -----------------------------------------------------
    def console(self, method, path, payload=None, timeout=30):
        return call(self.args.console, self.args.spn, method, path, payload, timeout)

    def checkin_payload(self):
        with self.lock:
            ev, self.events = self.events, []
        mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
        genesys = next(iter(self.apps), None)
        return {
            "device": self.args.device, "role": "laptop", "events": ev,
            "facts": {"os": "Windows laptop (simulated by Linux)", "shell": "cc-laptop-agent",
                      "desktop_open": self.desktop_open(), "genesys_running": self.app_running("genesys"),
                      "broker_reachable": self.broker_ok,
                      "metrics": {"cpu": self.cpu_percent(),
                                  "mem_pct": round(100 * (1 - mem["MemAvailable"] / mem["MemTotal"]), 1)}},
            "compliance": {
                "call_center_shell": {"ok": True, "detail": "laptop desktop replaced", "severity": "fail"},
                "broker_reachable": {"ok": self.broker_ok, "detail": "single sign-on broker", "severity": "fail"},
                "genesys_installed": {"ok": bool(genesys), "detail": "local Genesys app configured", "severity": "warn"},
            },
        }

    def handle(self, cmd):
        action, args = cmd["action"], cmd.get("args", {})
        png = None
        try:
            if action == "refresh":
                ok, out = True, "ok"
            elif action == "screenshot":
                png = self.screenshot()
                if args.get("first") == "1":
                    self.event("admin", "an administrator viewed the laptop screen")
                ok, out = True, "laptop screen"
            elif action == "message":
                # On Windows a message box appears; here it is logged where the agent would see it.
                self.event("message", f"message from IT: {args['text']}")
                ok, out = True, "shown to the agent"
            elif action == "start_app":
                ok = self.launch(args["app"])
                out = f"{args['app']} started or brought to front" if ok else "unknown app"
            elif action == "reconnect_desktop":
                self.reconnect = True
                self.close_desktop()
                ok, out = True, "reconnecting"
            elif action == "sign_out":
                self.signing_out = True
                self.close_desktop()
                ok, out = True, "signing out"
            else:
                ok, out = False, f"not supported on this laptop: {action}"
        except Exception as e:
            ok, out = False, f"{type(e).__name__}: {e}"
        res = {"id": cmd["id"], "ok": ok, "output": out}
        if png:
            res["image"] = base64.b64encode(png).decode()
        try:
            self.console("POST", "/agent/result", res)
        except Exception as e:
            print(f"cannot report to console: {e}", flush=True)

    def console_loop(self):
        def waiter():
            while True:
                try:
                    for c in self.console("GET", f"/agent/wait?device={self.args.device}&timeout=25", timeout=40)["commands"]:
                        threading.Thread(target=self.handle, args=(c,), daemon=True).start()
                except Exception:
                    time.sleep(5)
        threading.Thread(target=waiter, daemon=True).start()
        while True:
            try:
                for c in self.console("POST", "/agent/checkin", self.checkin_payload()).get("commands", []):
                    threading.Thread(target=self.handle, args=(c,), daemon=True).start()
            except Exception as e:
                print(f"console unreachable: {e}", flush=True)
            time.sleep(15)

    # ---- main loop ------------------------------------------------------
    def run(self):
        if self.args.console:
            threading.Thread(target=self.console_loop, daemon=True).start()
        if self.args.rdp:
            self.open_desktop()
        else:
            print("listening for laptop commands (Ctrl+C to stop)", flush=True)
        while True:
            if self.args.rdp and not self.desktop_open():
                if self.reconnect and not self.signing_out:
                    self.reconnect = False
                    self.event("desktop", "reconnecting the remote desktop")
                    self.open_desktop()
                    continue
                break
            try:
                msg = self.broker("GET", "/launch/wait?timeout=20", timeout=30)
                self.broker_ok = True
            except (urllib.error.URLError, OSError) as e:
                self.broker_ok = False
                print(f"broker unreachable: {e}", flush=True)
                time.sleep(5)
                continue
            for app in msg.get("apps", []):
                self.launch(app)
        self.event("desktop", "remote desktop closed: sign out")
        if self.args.console:
            try:
                self.console("POST", "/agent/checkin", self.checkin_payload())
            except Exception:
                pass
        sys.exit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", required=True, help="http://broker-01.example.local:8088")
    ap.add_argument("--spn", required=True, help="HTTP@broker-01.example.local")
    ap.add_argument("--rdp", help="RDP command; {user} and {password} are filled in")
    ap.add_argument("--app", action="append", default=[], metavar="ID=COMMAND",
                    help="local program for an app id, e.g. genesys='/opt/genesys/genesys'")
    ap.add_argument("--console", help="cc-console URL, e.g. http://broker-01.example.local:8090")
    ap.add_argument("--device", default=socket.gethostname().lower(), help="name shown in the console")
    ap.add_argument("--ticket-only", action="store_true", help="print a one-time ticket and exit")
    args = ap.parse_args()

    if args.ticket_only:
        try:
            print(json.dumps(call(args.broker, args.spn, "POST", "/sso/ticket")))
        except urllib.error.HTTPError as e:
            print(f"no ticket: HTTP {e.code} {e.read().decode(errors='replace')}", flush=True)
            sys.exit(2)
        return
    laptop = Laptop(args)
    try:
        laptop.run()
    except urllib.error.HTTPError as e:
        print(f"no ticket: HTTP {e.code} {e.read().decode(errors='replace')}", flush=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
