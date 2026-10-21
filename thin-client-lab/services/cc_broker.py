#!/usr/bin/env python3
"""cc-broker: links an agent's laptop to their session on the server farm.

1. Single sign-on. The laptop (already logged in to Windows with AD) asks for a
   one-time password with its Kerberos ticket (POST /sso/ticket). It opens the
   remote desktop with that password. The session host checks it here
   (POST /sso/verify) through pam_exec. The AD password is typed only once.
2. "Open on my laptop". An icon in the remote desktop (e.g. Genesys) asks the
   session host's relay to POST /launch {user, app}. The agent's laptop is
   waiting on GET /launch/wait (Kerberos) and starts the app locally.

Laptops authenticate with Kerberos, so a laptop only ever gets its own user's
tickets and commands. Session hosts authenticate with a shared host token.
Commands are app ids from an allow-list; the laptop maps them to its own
programs, so nothing arbitrary is ever executed on a laptop.
"""
import base64
import grp
import hmac
import json
import logging
import os
import pwd
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import gssapi

CONF = json.load(open(os.environ.get("CC_BROKER_CONF", "/etc/cc-broker/broker.json")))
TICKET_PREFIX = "ccsso-"            # session hosts only forward passwords with this prefix
TICKET_TTL = CONF.get("ticket_ttl", 120)
LAUNCH_TTL = CONF.get("launch_ttl", 30)  # a request nobody picked up by then is dropped
APPS = set(CONF["apps"])

log = logging.getLogger("cc-broker")
state = threading.Condition()
tickets = {}                        # one-time password -> (user, expiry)
pending = {}                        # user -> [(app id, expiry)]
stats = {"tickets": 0, "verify_ok": 0, "verify_fail": 0, "launches": 0, "waiting": 0}


def in_allowed_group(user):
    """Group check through SSSD on this AD-joined host."""
    try:
        gid = grp.getgrnam(CONF["allowed_group"]).gr_gid
        return gid in os.getgrouplist(user, pwd.getpwnam(user).pw_gid)
    except KeyError:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "cc-broker"

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    def reply(self, code, body=None, headers=()):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        if body is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def kerberos_user(self):
        """sAMAccountName of the Kerberos client, or None (a 401 has been sent)."""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Negotiate "):
            try:
                ctx = gssapi.SecurityContext(usage="accept")
                ctx.step(base64.b64decode(auth[len("Negotiate "):]))
                if ctx.complete:
                    return str(ctx.initiator_name).split("@")[0].lower()
            except gssapi.exceptions.GSSError as e:
                log.warning("kerberos rejected: %s", e)
        self.reply(401, {"error": "kerberos required"}, [("WWW-Authenticate", "Negotiate")])
        return None

    def from_session_host(self):
        token = self.headers.get("X-CC-Host-Token", "")
        if hmac.compare_digest(token.encode(), CONF["host_token"].encode()):
            return True
        self.reply(403, {"error": "unknown host"})
        return False

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/healthz":
            with state:
                return self.reply(200, dict(stats, open_tickets=len(tickets)))
        if url.path == "/launch/wait":
            user = self.kerberos_user()
            if not user:
                return
            timeout = min(float(parse_qs(url.query).get("timeout", ["25"])[0]), 55)
            with state:
                stats["waiting"] += 1
                state.wait_for(lambda: pending.get(user), timeout=timeout)
                now = time.time()
                taken = [(app, exp) for app, exp in pending.pop(user, []) if exp >= now]
                stats["waiting"] -= 1
            try:
                return self.reply(200, {"user": user, "apps": [app for app, _ in taken]})
            except OSError:
                # The laptop went away while waiting (e.g. agent restarted): give the
                # requests back so its next connection receives them.
                with state:
                    pending[user] = taken + pending.get(user, [])
                    state.notify_all()
                raise
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/sso/ticket":
            user = self.kerberos_user()
            if not user:
                return
            if not in_allowed_group(user):
                log.warning("ticket refused for %s: not in %s", user, CONF["allowed_group"])
                return self.reply(403, {"error": "not allowed"})
            otp = TICKET_PREFIX + secrets.token_urlsafe(24)
            with state:
                now = time.time()
                for k in [k for k, (_, exp) in tickets.items() if exp < now]:
                    del tickets[k]
                tickets[otp] = (user, now + TICKET_TTL)
                stats["tickets"] += 1
            log.info("ticket issued for %s", user)
            return self.reply(200, {"username": user, "password": otp, "expires_in": TICKET_TTL})

        if path == "/sso/verify":
            if not self.from_session_host():
                return
            req = self.body()
            with state:
                user, exp = tickets.pop(req.get("password", ""), (None, 0))   # single use
                ok = user is not None and user == req.get("user", "").lower() and exp >= time.time()
                stats["verify_ok" if ok else "verify_fail"] += 1
            log.info("verify %s for %s", "ok" if ok else "FAILED", req.get("user"))
            return self.reply(200 if ok else 403, {"ok": ok})

        if path == "/launch":
            if not self.from_session_host():
                return
            req = self.body()
            user, app = req.get("user", "").lower(), req.get("app", "")
            if app not in APPS or not user:
                return self.reply(400, {"error": f"unknown app {app!r}"})
            with state:
                now = time.time()
                queue = [(a, exp) for a, exp in pending.get(user, []) if exp >= now]
                pending[user] = queue + [(app, now + LAUNCH_TTL)]
                stats["launches"] += 1
                state.notify_all()
            log.info("launch %s on %s's laptop", app, user)
            return self.reply(202, {"queued": app})

        self.reply(404, {"error": "not found"})


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = ThreadingHTTPServer((CONF.get("listen", "0.0.0.0"), CONF.get("port", 8088)), Handler)
    server.daemon_threads = True
    if CONF.get("tls_cert"):
        # Production: tickets travel in the reply, so serve HTTPS with a certificate
        # from the internal CA that the laptops trust.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(CONF["tls_cert"], CONF["tls_key"])
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    log.info("listening on %s:%s%s", *server.server_address, " (TLS)" if CONF.get("tls_cert") else "")
    server.serve_forever()


if __name__ == "__main__":
    main()
