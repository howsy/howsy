#!/usr/bin/env python3
"""cc-console: management dashboard for the call-center fleet.

Devices check in here (session hosts, endpoints and the broker through
cc-device-agent; agents' laptops through the laptop shell) with their health,
compliance and sessions, and pick up commands the admins queue. Admins sign in
with their AD account (members of the admin group only) and get:

  overview   fleet health, alerts, capacity
  devices    inventory, compliance, remote actions, live screen, timeline
  sessions   who works where, CPU/RAM per agent, watch / message / log off
  apps       the catalog of web systems on the agent desktop, versions and
             rollback, published by ring (pilot first, then broad)
  policies   browser URL rules and the outbound firewall for agent users on
             session hosts and endpoints, published by ring the same way
  activity   device timeline and the admins' audit log

Device auth: a shared enrollment token (Linux agents, root) or Kerberos (the
laptop shell runs as the logged-in user). Admin auth: AD password checked with
Kerberos, group membership through SSSD; session cookie + CSRF header.
"""
import base64
import collections
import grp
import hmac
import ipaddress
import json
import logging
import os
import pwd
import re
import secrets
import sqlite3
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import gssapi

CONF = json.load(open(os.environ.get("CC_CONSOLE_CONF", "/etc/cc-console/console.json")))
UI_DIR = CONF.get("ui_dir", "/usr/local/share/cc-console/ui")
ONLINE_SECONDS = 60
SESSION_HOURS = 8
UI_FILES = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}

# What an admin may ask a device to do, by device role. Devices only ever run
# actions from this list, with their own fixed implementation.
ACTIONS = {
    "refresh":           {"roles": "*", "label": "Refresh now"},
    "message":           {"roles": ["session_host", "laptop"], "label": "Send message", "args": ["text"]},
    "screenshot":        {"roles": ["laptop"], "label": "View screen"},
    "screenshot_user":   {"roles": ["session_host"], "label": "View session", "args": ["user"]},
    "logoff_user":       {"roles": ["session_host"], "label": "Log user off", "args": ["user"], "confirm": True},
    "start_app":         {"roles": ["laptop"], "label": "Start Genesys", "args": ["app"]},
    "reconnect_desktop": {"roles": ["laptop"], "label": "Reconnect desktop"},
    "sign_out":          {"roles": ["laptop"], "label": "Sign out", "confirm": True},
    "collect_logs":      {"roles": ["session_host", "broker", "endpoint"], "label": "Collect logs"},
    "restart_service":   {"roles": ["session_host", "broker"], "label": "Restart service", "args": ["service"],
                          "confirm": True},
    "apply_catalog":     {"roles": ["session_host"], "label": "Re-apply desktop"},
    "apply_policy":      {"roles": ["session_host", "endpoint"], "label": "Re-apply policies"},
    "reboot":            {"roles": ["session_host", "broker", "endpoint"], "label": "Reboot", "confirm": True},
}
RINGS = ["pilot", "broad"]
POLICY_ROLES = ("session_host", "endpoint")
EMPTY_POLICY = {"url_mode": "block_listed", "url_block": [], "url_allow": [],
                "egress_mode": "off", "egress_allow": [], "limits": {"cpu_pct": 0, "mem_mb": 0},
                "assignments": []}
TARGET = re.compile(r"^(group|user):([A-Za-z0-9._@ -]{1,64})$")
# Things published by ring: table of versions, table of ring -> version, content column.
VERSIONED = {"catalog": ("catalog_versions", "rings", "items"),
             "policy": ("policy_versions", "policy_rings", "policy")}
URL_PATTERN = re.compile(r"^[A-Za-z0-9*.\-:/_\[\]?=&%#~+@]{1,300}$")

log = logging.getLogger("cc-console")
lock = threading.RLock()
wake = threading.Condition(lock)
db = sqlite3.connect(CONF.get("db", "/var/lib/cc-console/console.db"), check_same_thread=False)
db.row_factory = sqlite3.Row
admin_sessions = {}                         # cookie -> (user, expiry)
login_failures = collections.defaultdict(list)
metrics = collections.defaultdict(lambda: collections.deque(maxlen=120))  # device -> (ts, cpu, mem%)


def init_db():
    with lock:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, role TEXT, ring TEXT DEFAULT 'broad',
            first_seen REAL, last_seen REAL, ip TEXT, facts TEXT, compliance TEXT, sessions TEXT,
            online INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, device TEXT, ts REAL, kind TEXT,
            message TEXT, user TEXT);
        CREATE TABLE IF NOT EXISTS commands (id INTEGER PRIMARY KEY, device TEXT, action TEXT, args TEXT,
            status TEXT, created_by TEXT, created_at REAL, sent_at REAL, done_at REAL, result TEXT,
            has_image INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS images (command_id INTEGER PRIMARY KEY, png BLOB);
        CREATE TABLE IF NOT EXISTS catalog_versions (version INTEGER PRIMARY KEY, created_at REAL,
            created_by TEXT, note TEXT, items TEXT);
        CREATE TABLE IF NOT EXISTS rings (ring TEXT PRIMARY KEY, version INTEGER);
        CREATE TABLE IF NOT EXISTS policy_versions (version INTEGER PRIMARY KEY, created_at REAL,
            created_by TEXT, note TEXT, policy TEXT);
        CREATE TABLE IF NOT EXISTS policy_rings (ring TEXT PRIMARY KEY, version INTEGER);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, ts REAL, admin TEXT, action TEXT,
            target TEXT, detail TEXT);
        CREATE INDEX IF NOT EXISTS events_device ON events(device, ts);
        """)
        seed = CONF.get("seed_catalog")
        seeds = {"catalog": ("initial catalog", json.load(open(seed)) if seed and os.path.exists(seed) else []),
                 "policy": ("no extra rules", EMPTY_POLICY)}
        for kind, (versions, rings, col) in VERSIONED.items():
            if not db.execute(f"SELECT 1 FROM {versions}").fetchone():
                note, content = seeds[kind]
                db.execute(f"INSERT INTO {versions} VALUES (1, ?, 'setup', ?, ?)", (time.time(), note, json.dumps(content)))
                for ring in RINGS:
                    db.execute(f"INSERT OR REPLACE INTO {rings} VALUES (?, 1)", (ring,))
        db.commit()


def add_event(device, kind, message, user=None, ts=None):
    with lock:
        db.execute("INSERT INTO events (device, ts, kind, message, user) VALUES (?,?,?,?,?)",
                   (device, ts or time.time(), kind, message, user))
        db.commit()


def audit(admin, action, target, detail=""):
    with lock:
        db.execute("INSERT INTO audit (ts, admin, action, target, detail) VALUES (?,?,?,?,?)",
                   (time.time(), admin, action, target, detail))
        db.commit()


def ring_content(kind, ring):
    """{"version": n, <content column>: ...} that devices in this ring should have."""
    versions, rings, col = VERSIONED[kind]
    with lock:
        row = db.execute(f"SELECT v.version, v.{col} FROM {rings} r JOIN {versions} v ON v.version = r.version "
                         "WHERE r.ring = ?", (ring,)).fetchone()
    return {"version": row["version"], col: json.loads(row[col])} if row else None


def ring_catalog(ring):
    return ring_content("catalog", ring)


def ring_versions(kind):
    versions, rings, col = VERSIONED[kind]
    with lock:
        vs = [dict(r, **{col: json.loads(r[col])}) for r in
              db.execute(f"SELECT * FROM {versions} ORDER BY version DESC LIMIT 30")]
        rs = {r["ring"]: r["version"] for r in db.execute(f"SELECT * FROM {rings}")}
    return {"versions": vs, "rings": rs}


def publish_version(kind, user, content, note):
    """New version, live on the pilot ring only."""
    versions, rings, _ = VERSIONED[kind]
    with lock:
        v = (db.execute(f"SELECT MAX(version) FROM {versions}").fetchone()[0] or 0) + 1
        db.execute(f"INSERT INTO {versions} VALUES (?,?,?,?,?)",
                   (v, time.time(), user, str(note or "")[:200], json.dumps(content)))
        db.execute(f"UPDATE {rings} SET version=? WHERE ring='pilot'", (v,))
        db.commit()
    return v


def promote_version(kind):
    _, rings, _ = VERSIONED[kind]
    with lock:
        v = db.execute(f"SELECT version FROM {rings} WHERE ring='pilot'").fetchone()["version"]
        db.execute(f"UPDATE {rings} SET version=? WHERE ring='broad'", (v,))
        db.commit()
    return v


def rollback_version(kind, v, ring):
    versions, rings, _ = VERSIONED[kind]
    if ring not in RINGS + ["all"]:
        raise ValueError("unknown ring")
    with lock:
        if not db.execute(f"SELECT 1 FROM {versions} WHERE version=?", (v,)).fetchone():
            raise ValueError("unknown version")
        for r in (RINGS if ring == "all" else [ring]):
            db.execute(f"UPDATE {rings} SET version=? WHERE ring=?", (v, r))
        db.commit()


def clean_ports(text):
    """'' (any port) or '80,443,8000-8100' -> normalised string; ValueError if bad."""
    text = str(text or "").replace(" ", "")
    if not text:
        return ""
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        nums = [int(lo)] + ([int(hi)] if hi else [])
        if not all(1 <= n <= 65535 for n in nums) or (hi and nums[1] < nums[0]):
            raise ValueError(f"bad port {part!r}")
    return text


def clean_rules(raw):
    rules = []
    for r in (raw or [])[:200]:
        dest = str(r.get("dest", "")).strip()
        try:
            dest = str(ipaddress.ip_network(dest, strict=False))
        except ValueError:
            raise ValueError(f"destination must be an IP address or network (CIDR): {dest!r}") from None
        proto = r.get("proto", "tcp")
        if proto not in ("tcp", "udp"):
            raise ValueError("protocol must be tcp or udp")
        rules.append({"name": str(r.get("name", "")).strip()[:60], "dest": dest, "proto": proto,
                      "ports": clean_ports(r.get("ports", ""))})
    return rules


def clean_limits(raw):
    """Per-user CPU (% of one core, 100 = one core) and RAM (MB); 0 = server default."""
    raw = raw or {}
    cpu, mem = int(raw.get("cpu_pct") or 0), int(raw.get("mem_mb") or 0)
    if not 0 <= cpu <= 6400:
        raise ValueError("CPU limit must be 0-6400 % of one core")
    if mem and not 256 <= mem <= 1048576:
        raise ValueError("RAM limit must be at least 256 MB")
    return {"cpu_pct": cpu, "mem_mb": mem}


def clean_policy(req):
    """Validate an admin's policy; devices get exactly this, nothing free-form.

    The top level applies to every agent user. `assignments` override the
    firewall and the resource limits for an AD group or one user; a user
    assignment beats a group one, and among groups the first listed wins.
    URL rules stay per machine (Chrome on Linux has machine-wide policy only)."""
    url_mode = req.get("url_mode", "block_listed")
    egress_mode = req.get("egress_mode", "off")
    if url_mode not in ("block_listed", "allow_only"):
        raise ValueError("url_mode must be block_listed or allow_only")
    if egress_mode not in ("off", "allow_only"):
        raise ValueError("egress_mode must be off or allow_only")
    lists = {}
    for key in ("url_block", "url_allow"):
        vals = [str(u).strip() for u in req.get(key, []) if str(u).strip()]
        bad = [u for u in vals if not URL_PATTERN.match(u)]
        if bad:
            raise ValueError(f"not a URL pattern: {bad[0]!r}")
        lists[key] = list(dict.fromkeys(vals))[:500]
    rules = clean_rules(req.get("egress_allow"))
    if egress_mode == "allow_only" and not rules:
        raise ValueError("allow-only firewall needs at least one allowed destination")
    assignments, seen = [], set()
    for a in (req.get("assignments") or [])[:50]:
        target = str(a.get("target", "")).strip().lower()
        if not TARGET.match(target):
            raise ValueError(f"assignment target must be group:<AD group> or user:<username>, not {target!r}")
        if target in seen:
            raise ValueError(f"{target} is assigned twice")
        seen.add(target)
        mode = a.get("egress_mode", "inherit")
        if mode not in ("inherit", "off", "allow_only"):
            raise ValueError("assignment firewall must be inherit, off or allow_only")
        a_rules = clean_rules(a.get("egress_allow"))
        if mode == "allow_only" and not a_rules:
            raise ValueError(f"{target}: allow-only firewall needs at least one allowed destination")
        assignments.append({"target": target, "egress_mode": mode, "egress_allow": a_rules,
                            "limits": clean_limits(a.get("limits"))})
    return {"url_mode": url_mode, **lists, "egress_mode": egress_mode, "egress_allow": rules,
            "limits": clean_limits(req.get("limits")), "assignments": assignments}


def compliance_state(comp):
    """compliant | warning | noncompliant | unknown, from the device's checks."""
    if not comp:
        return "unknown"
    fails = [k for k, c in comp.items() if c.get("ok") is False and c.get("severity", "fail") == "fail"]
    warns = [k for k, c in comp.items() if c.get("ok") is False and c.get("severity") == "warn"]
    return "noncompliant" if fails else "warning" if warns else "compliant"


def device_row(r):
    facts = json.loads(r["facts"] or "{}")
    comp = json.loads(r["compliance"] or "{}")
    online = time.time() - (r["last_seen"] or 0) < ONLINE_SECONDS
    return {"id": r["id"], "role": r["role"], "ring": r["ring"], "ip": r["ip"], "online": online,
            "last_seen": r["last_seen"], "first_seen": r["first_seen"], "facts": facts, "compliance": comp,
            "compliance_state": compliance_state(comp), "sessions": json.loads(r["sessions"] or "[]"),
            "user": facts.get("user")}


def all_devices():
    with lock:
        return [device_row(r) for r in db.execute("SELECT * FROM devices ORDER BY role, id")]


def with_laptops(host, devices):
    """A session host's desktops, each with the laptop it is viewed from (matched by user)."""
    laptops = {d["user"]: d for d in devices if d["role"] == "laptop" and d["online"] and d.get("user")}
    out = []
    for s in host["sessions"]:
        lap = laptops.get(s["user"])
        out.append(dict(s, host=host["id"], laptop=lap["id"] if lap else None,
                        genesys=lap["facts"].get("genesys_running") if lap else None))
    return out


def alerts(devices):
    out = []
    for d in devices:
        if not d["online"]:
            out.append({"level": "critical" if d["role"] in ("session_host", "broker") else "warning",
                        "device": d["id"], "text": "offline"})
            continue
        if d["compliance_state"] == "noncompliant":
            bad = [k for k, c in d["compliance"].items() if c.get("ok") is False and c.get("severity", "fail") == "fail"]
            out.append({"level": "warning", "device": d["id"], "text": "not compliant: " + ", ".join(bad)})
        pol = d["facts"].get("policy") or {}
        if pol.get("error"):
            out.append({"level": "warning", "device": d["id"], "text": f"policies v{pol.get('version')} not applied: {pol['error']}"})
        m = d["facts"].get("metrics", {})
        if d["role"] == "session_host" and m.get("cpu", 0) > 85:
            out.append({"level": "warning", "device": d["id"], "text": f"CPU {m['cpu']:.0f}%"})
        if d["role"] == "session_host" and m.get("mem_pct", 0) > 90:
            out.append({"level": "warning", "device": d["id"], "text": f"memory {m['mem_pct']:.0f}%"})
    return out


def janitor():
    """Marks devices online/offline (with timeline events), expires commands, prunes images."""
    while True:
        time.sleep(10)
        now = time.time()
        with lock:
            for r in db.execute("SELECT id, last_seen, online FROM devices").fetchall():
                online = now - (r["last_seen"] or 0) < ONLINE_SECONDS
                if bool(r["online"]) != online:
                    db.execute("UPDATE devices SET online=? WHERE id=?", (int(online), r["id"]))
                    db.execute("INSERT INTO events (device, ts, kind, message) VALUES (?,?,?,?)",
                               (r["id"], now, "status", "came online" if online else "went offline"))
            db.execute("UPDATE commands SET status='expired' WHERE status IN ('queued','sent') AND created_at < ?",
                       (now - 180,))
            db.execute("DELETE FROM images WHERE command_id IN (SELECT id FROM commands WHERE created_at < ?)",
                       (now - 3600,))
            db.execute("DELETE FROM events WHERE ts < ?", (now - 14 * 86400,))
            db.commit()


def kerberos_principal(header):
    if not header.startswith("Negotiate "):
        return None
    try:
        ctx = gssapi.SecurityContext(usage="accept")
        ctx.step(base64.b64decode(header[len("Negotiate "):]))
        return str(ctx.initiator_name).split("@")[0].lower() if ctx.complete else None
    except gssapi.exceptions.GSSError as e:
        log.warning("kerberos rejected: %s", e)
        return None


def check_ad_password(user, password):
    try:
        name = gssapi.Name(f"{user}@{CONF['realm']}", gssapi.NameType.kerberos_principal)
        gssapi.raw.acquire_cred_with_password(name, password.encode(), usage="initiate")
        return True
    except gssapi.exceptions.GSSError:
        return False


def in_group(user, group):
    try:
        gid = grp.getgrnam(group).gr_gid
        return gid in os.getgrouplist(user, pwd.getpwnam(user).pw_gid)
    except KeyError:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "cc-console"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---- plumbing -------------------------------------------------------
    def send(self, code, body=b"", ctype="application/json", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'")
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 8 * 1024 * 1024:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def admin(self):
        """Signed-in admin for this request, or None (401 already sent)."""
        cookie = self.headers.get("Cookie", "")
        token = next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("ccconsole=")), "")
        with lock:
            user, exp = admin_sessions.get(token, (None, 0))
        if not user or exp < time.time():
            self.send(401, {"error": "sign in"})
            return None
        if self.command == "POST" or self.command == "PUT":
            if self.headers.get("X-CC-Console") != "1":       # CSRF: custom header needs same origin
                self.send(403, {"error": "missing X-CC-Console header"})
                return None
        return user

    def device_auth(self):
        """(auth kind, user) for a device request, or None (error sent)."""
        token = self.headers.get("X-CC-Device-Token", "")
        if token and hmac.compare_digest(token.encode(), CONF["device_token"].encode()):
            return ("token", None)
        user = kerberos_principal(self.headers.get("Authorization", ""))
        if user:
            return ("kerberos", user)
        if token:
            self.send(403, {"error": "bad device token"})
        else:
            self.send(401, {"error": "auth required"}, headers=[("WWW-Authenticate", "Negotiate")])
        return None

    # ---- routing --------------------------------------------------------
    def do_GET(self):
        url = urlparse(self.path)
        p, q = url.path, parse_qs(url.query)
        if p in UI_FILES:
            name = UI_FILES[p]
            data = open(os.path.join(UI_DIR, name), "rb").read()
            return self.send(200, data, MIME[os.path.splitext(name)[1]])
        if p == "/healthz":
            return self.send(200, {"ok": True})
        if p == "/favicon.ico":
            return self.send(204, b"", "image/x-icon")
        if p == "/agent/wait":
            return self.agent_wait(q)
        if not p.startswith("/api/"):
            return self.send(404, {"error": "not found"})
        user = self.admin()
        if not user:
            return
        if p == "/api/me":
            return self.send(200, {"user": user})
        if p == "/api/summary":
            return self.api_summary()
        if p == "/api/devices":
            return self.send(200, all_devices())
        if p.startswith("/api/devices/"):
            return self.api_device(p.split("/")[3])
        if p == "/api/sessions":
            return self.api_sessions()
        if p.startswith("/api/commands/"):
            parts = p.split("/")
            return self.api_command_image(int(parts[3])) if len(parts) > 4 and parts[4] == "image" \
                else self.api_command(int(parts[3]))
        if p == "/api/events":
            return self.api_events(q)
        if p == "/api/catalog":
            return self.send(200, ring_versions("catalog"))
        if p == "/api/policy":
            return self.api_policy()
        if p == "/api/audit":
            with lock:
                rows = [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200")]
            return self.send(200, rows)
        if p == "/api/actions":
            return self.send(200, ACTIONS)
        self.send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            if p == "/api/login":
                return self.api_login()
            if p == "/agent/checkin":
                return self.agent_checkin()
            if p == "/agent/result":
                return self.agent_result()
            if not p.startswith("/api/"):
                return self.send(404, {"error": "not found"})
            user = self.admin()
            if not user:
                return
            if p == "/api/logout":
                return self.api_logout()
            if p.startswith("/api/devices/") and p.endswith("/actions"):
                return self.api_action(user, p.split("/")[3])
            if p.startswith("/api/devices/") and p.endswith("/ring"):
                return self.api_set_ring(user, p.split("/")[3])
            if p == "/api/catalog":
                return self.api_publish(user)
            if p == "/api/catalog/promote":
                return self.api_promote(user)
            if p == "/api/catalog/rollback":
                return self.api_rollback(user)
            if p == "/api/policy":
                return self.api_policy_publish(user)
            if p == "/api/policy/promote":
                v = promote_version("policy")
                audit(user, "promoted policies", f"v{v}", "pilot -> broad")
                return self.send(200, {"version": v})
            if p == "/api/policy/rollback":
                req = self.body()
                v, ring = int(req["version"]), req.get("ring", "all")
                rollback_version("policy", v, ring)
                audit(user, "rolled back policies", f"v{v}", f"ring {ring}")
                return self.send(200, {"version": v})
            self.send(404, {"error": "not found"})
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            self.send(400, {"error": str(e)})

    do_PUT = do_POST

    # ---- admin sign-in --------------------------------------------------
    def api_login(self):
        ip = self.client_address[0]
        now = time.time()
        with lock:
            login_failures[ip] = [t for t in login_failures[ip] if t > now - 300]
            if len(login_failures[ip]) >= 5:
                return self.send(429, {"error": "too many attempts, wait 5 minutes"})
        req = self.body()
        user = req.get("username", "").strip().lower().split("\\")[-1].split("@")[0]
        ok = bool(user) and check_ad_password(user, req.get("password", "")) and in_group(user, CONF["admin_group"])
        if not ok:
            with lock:
                login_failures[ip].append(now)
            audit(user or "?", "login failed", ip)
            return self.send(403, {"error": "wrong password, or not a member of " + CONF["admin_group"]})
        token = secrets.token_urlsafe(32)
        with lock:
            admin_sessions[token] = (user, now + SESSION_HOURS * 3600)
        audit(user, "signed in", ip)
        secure = "; Secure" if CONF.get("tls_cert") else ""
        self.send(200, {"user": user}, headers=[
            ("Set-Cookie", f"ccconsole={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_HOURS*3600}{secure}")])

    def api_logout(self):
        cookie = self.headers.get("Cookie", "")
        token = next((c.split("=", 1)[1] for c in cookie.split("; ") if c.startswith("ccconsole=")), "")
        with lock:
            admin_sessions.pop(token, None)
        self.send(200, {"ok": True}, headers=[("Set-Cookie", "ccconsole=; Max-Age=0; Path=/")])

    # ---- device API -----------------------------------------------------
    def agent_checkin(self):
        auth = self.device_auth()
        if not auth:
            return
        req = self.body()
        dev = str(req["device"]).lower()[:64]
        role = req.get("role", "unknown")
        now = time.time()
        facts = req.get("facts", {})
        if auth[0] == "kerberos":
            facts["user"] = auth[1]                       # the user is whoever Kerberos says it is
        comp = req.get("compliance", {})
        with lock:
            old = db.execute("SELECT compliance FROM devices WHERE id=?", (dev,)).fetchone()
            if old is None:
                db.execute("INSERT INTO devices (id, role, first_seen) VALUES (?,?,?)", (dev, role, now))
                db.execute("INSERT INTO events (device, ts, kind, message) VALUES (?,?,?,?)",
                           (dev, now, "status", f"enrolled as {role}"))
            else:
                before = compliance_state(json.loads(old["compliance"] or "{}"))
                after = compliance_state(comp)
                if before != after and before != "unknown":
                    db.execute("INSERT INTO events (device, ts, kind, message) VALUES (?,?,?,?)",
                               (dev, now, "compliance", f"compliance: {before} -> {after}"))
            db.execute("UPDATE devices SET role=?, last_seen=?, ip=?, facts=?, compliance=?, sessions=? WHERE id=?",
                       (role, now, self.client_address[0], json.dumps(facts), json.dumps(comp),
                        json.dumps(req.get("sessions", [])), dev))
            for e in req.get("events", [])[:50]:
                db.execute("INSERT INTO events (device, ts, kind, message, user) VALUES (?,?,?,?,?)",
                           (dev, float(e.get("ts", now)), e.get("kind", "device")[:32],
                            str(e.get("message", ""))[:500], e.get("user") or facts.get("user")))
            ring = db.execute("SELECT ring FROM devices WHERE id=?", (dev,)).fetchone()["ring"]
            db.commit()
        m = facts.get("metrics", {})
        metrics[dev].append((now, m.get("cpu"), m.get("mem_pct")))
        reply = {"ring": ring, "commands": self.take_commands(dev)}
        if role == "session_host":
            reply["catalog"] = ring_catalog(ring)
        if role in POLICY_ROLES:
            reply["policy"] = ring_content("policy", ring)
        self.send(200, reply)

    def take_commands(self, dev):
        with lock:
            rows = db.execute("SELECT id, action, args FROM commands WHERE device=? AND status='queued' ORDER BY id",
                              (dev,)).fetchall()
            if rows:
                db.executemany("UPDATE commands SET status='sent', sent_at=? WHERE id=?",
                               [(time.time(), r["id"]) for r in rows])
                db.commit()
        return [{"id": r["id"], "action": r["action"], "args": json.loads(r["args"] or "{}")} for r in rows]

    def agent_wait(self, q):
        if not self.device_auth():
            return
        dev = q.get("device", [""])[0].lower()
        timeout = min(float(q.get("timeout", ["20"])[0]), 55)
        with wake:
            wake.wait_for(lambda: db.execute("SELECT 1 FROM commands WHERE device=? AND status='queued'",
                                             (dev,)).fetchone(), timeout=timeout)
        self.send(200, {"commands": self.take_commands(dev)})

    def agent_result(self):
        if not self.device_auth():
            return
        req = self.body()
        cid = int(req["id"])
        png = base64.b64decode(req["image"]) if req.get("image") else None
        with lock:
            row = db.execute("SELECT device, action FROM commands WHERE id=?", (cid,)).fetchone()
            if not row:
                return self.send(404, {"error": "unknown command"})
            db.execute("UPDATE commands SET status=?, done_at=?, result=?, has_image=? WHERE id=?",
                       ("done" if req.get("ok") else "failed", time.time(), str(req.get("output", ""))[:65536],
                        int(png is not None), cid))
            if png:
                db.execute("INSERT OR REPLACE INTO images VALUES (?, ?)", (cid, png))
            db.commit()
        self.send(200, {"ok": True})

    # ---- admin API ------------------------------------------------------
    def api_summary(self):
        devs = all_devices()
        by_role = collections.Counter(d["role"] for d in devs)
        sessions = sum(len(d["sessions"]) for d in devs if d["role"] == "session_host" and d["online"])
        hosts = [d for d in devs if d["role"] == "session_host"]
        capacity = [{"id": d["id"], "online": d["online"], "sessions": len(d["sessions"]),
                     "cpu": d["facts"].get("metrics", {}).get("cpu"), "mem_pct": d["facts"].get("metrics", {}).get("mem_pct"),
                     "history": [x for x in metrics[d["id"]]][-60:]} for d in hosts]
        self.send(200, {
            "devices": len(devs), "online": sum(d["online"] for d in devs),
            "compliant": sum(d["compliance_state"] == "compliant" for d in devs),
            "noncompliant": sum(d["compliance_state"] == "noncompliant" for d in devs),
            "warning": sum(d["compliance_state"] == "warning" for d in devs),
            "by_role": by_role, "sessions": sessions,
            "laptops_online": sum(d["online"] for d in devs if d["role"] == "laptop"),
            "alerts": alerts(devs), "capacity": capacity,
            "catalog": {r: (ring_catalog(r) or {}).get("version") for r in RINGS},
            "policy": {r: (ring_content("policy", r) or {}).get("version") for r in RINGS},
        })

    def api_device(self, dev):
        with lock:
            r = db.execute("SELECT * FROM devices WHERE id=?", (dev,)).fetchone()
            if not r:
                return self.send(404, {"error": "unknown device"})
            d = device_row(r)
            d["events"] = [dict(e) for e in db.execute(
                "SELECT ts, kind, message, user FROM events WHERE device=? ORDER BY ts DESC LIMIT 100", (dev,))]
            d["commands"] = [dict(c) for c in db.execute(
                "SELECT id, action, args, status, created_by, created_at, done_at, has_image, "
                "substr(result, 1, 300) AS result FROM commands WHERE device=? ORDER BY id DESC LIMIT 30", (dev,))]
        d["history"] = list(metrics[dev])[-60:]
        if d["role"] == "session_host":
            d["sessions"] = with_laptops(d, all_devices())
        d["actions"] = {k: v for k, v in ACTIONS.items() if v["roles"] == "*" or d["role"] in v["roles"]}
        self.send(200, d)

    def api_sessions(self):
        devs = all_devices()
        out = []
        for d in devs:
            if d["role"] == "session_host" and d["online"]:
                out += with_laptops(d, devs)
        self.send(200, sorted(out, key=lambda s: s["user"]))

    def api_action(self, user, dev):
        req = self.body()
        action, args = req.get("action"), req.get("args", {})
        spec = ACTIONS.get(action)
        with lock:
            r = db.execute("SELECT role FROM devices WHERE id=?", (dev,)).fetchone()
        if not r or not spec or (spec["roles"] != "*" and r["role"] not in spec["roles"]):
            return self.send(400, {"error": f"action {action!r} not available for this device"})
        for a in spec.get("args", []):
            if not str(args.get(a, "")).strip():
                return self.send(400, {"error": f"missing {a}"})
        args = {k: str(v)[:1000] for k, v in args.items()}
        with wake:
            cur = db.execute("INSERT INTO commands (device, action, args, status, created_by, created_at) "
                             "VALUES (?,?,?,?,?,?)", (dev, action, json.dumps(args), "queued", user, time.time()))
            db.commit()
            wake.notify_all()
        # Screen views are frequent (live view refresh): audit the first one per minute only.
        if not action.startswith("screenshot") or args.get("first") == "1":
            audit(user, action, dev, json.dumps(args))
            add_event(dev, "admin", f"{user}: {spec['label']}" + (f" ({args.get('user')})" if args.get("user") else ""), user)
        self.send(200, {"id": cur.lastrowid})

    def api_command(self, cid):
        with lock:
            r = db.execute("SELECT * FROM commands WHERE id=?", (cid,)).fetchone()
        self.send(200, dict(r) if r else {"error": "unknown"})

    def api_command_image(self, cid):
        with lock:
            r = db.execute("SELECT png FROM images WHERE command_id=?", (cid,)).fetchone()
        if not r:
            return self.send(404, {"error": "no image"})
        self.send(200, r["png"], "image/png")

    def api_events(self, q):
        dev = q.get("device", [None])[0]
        limit = min(int(q.get("limit", ["200"])[0]), 1000)
        sql = "SELECT device, ts, kind, message, user FROM events"
        params = []
        if dev:
            sql += " WHERE device=?"
            params.append(dev)
        with lock:
            rows = [dict(r) for r in db.execute(sql + " ORDER BY ts DESC LIMIT ?", params + [limit])]
        self.send(200, rows)

    def api_publish(self, user):
        req = self.body()
        items = []
        for it in req.get("items", []):
            name, url = str(it.get("name", "")).strip(), str(it.get("url", "")).strip()
            if not name or not url.startswith(("http://", "https://")):
                raise ValueError(f"each system needs a name and an http(s) URL ({name or url!r})")
            items.append({"name": name[:60], "url": url[:500], "icon": str(it.get("icon") or "applications-internet")[:60]})
        v = publish_version("catalog", user, items, req.get("note"))
        audit(user, "published catalog", f"v{v}", f"{len(items)} systems, to pilot ring")
        self.send(200, {"version": v, "ring": "pilot"})

    def api_promote(self, user):
        v = promote_version("catalog")
        audit(user, "promoted catalog", f"v{v}", "pilot -> broad")
        self.send(200, {"version": v})

    def api_rollback(self, user):
        req = self.body()
        v, ring = int(req["version"]), req.get("ring", "all")
        rollback_version("catalog", v, ring)
        audit(user, "rolled back catalog", f"v{v}", f"ring {ring}")
        self.send(200, {"version": v})

    def api_policy(self):
        out = ring_versions("policy")
        out["devices"] = [{"id": d["id"], "role": d["role"], "ring": d["ring"], "online": d["online"],
                           "applied": d["facts"].get("policy")} for d in all_devices() if d["role"] in POLICY_ROLES]
        self.send(200, out)

    def api_policy_publish(self, user):
        req = self.body()
        pol = clean_policy(req)
        v = publish_version("policy", user, pol, req.get("note"))
        audit(user, "published policies", f"v{v}",
              f"URLs {pol['url_mode']} ({len(pol['url_block'])} blocked, {len(pol['url_allow'])} allowed); "
              f"firewall {pol['egress_mode']} ({len(pol['egress_allow'])} rules); "
              f"{len(pol['assignments'])} group/user assignments; to pilot ring")
        self.send(200, {"version": v, "ring": "pilot"})

    def api_set_ring(self, user, dev):
        ring = self.body().get("ring")
        if ring not in RINGS:
            raise ValueError("unknown ring")
        with lock:
            db.execute("UPDATE devices SET ring=? WHERE id=?", (ring, dev))
            db.commit()
        audit(user, "moved device", dev, f"to ring {ring}")
        self.send(200, {"ring": ring})


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    init_db()
    threading.Thread(target=janitor, daemon=True).start()
    server = ThreadingHTTPServer((CONF.get("listen", "0.0.0.0"), CONF.get("port", 8090)), Handler)
    server.daemon_threads = True
    if CONF.get("tls_cert"):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(CONF["tls_cert"], CONF["tls_key"])
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    log.info("console on %s:%s", *server.server_address)
    server.serve_forever()


if __name__ == "__main__":
    main()
