#!/usr/bin/env python3
"""cc-device-agent: reports a Linux machine to cc-console and carries out the
admin actions it is sent (runs as root on session hosts, endpoints, broker).

Every 15 s: health (CPU, RAM, disk), compliance checks for its role, agent
sessions (session hosts), events. In between it waits on the console for
commands, so actions arrive within a second. Session hosts also receive the
desktop catalog for their ring and re-render the icons and browser tabs.
Session hosts and endpoints receive the policies for their ring: browser URL
rules (an extra Chrome managed-policy file) and an outbound firewall for agent
users' traffic (nftables; root and system services are never filtered).
"""
import base64
import grp
import json
import os
import pwd
import re
import socket
import subprocess
import threading
import time
import urllib.request

CONF = json.load(open(os.environ.get("CC_AGENT_CONF", "/etc/cc-console/agent.json")))
ROLE = CONF["role"]
DEVICE = CONF.get("device") or socket.gethostname().lower()
VERSION = "1.1"
CATALOG = "/etc/cc-desktop/catalog.json"
POLICY_ROLES = ("session_host", "endpoint")
POLICY = "/etc/cc-console/policy.json"           # last policy received, re-applied at boot
NFT_RULES = "/etc/cc-console/cc-policy.nft"
CHROME_DIR = "/etc/opt/chrome/policies/managed"
# Chrome reads every file in CHROME_DIR; for a policy set in two files the file
# that sorts last wins. cc-urls.json sorts after cc-policy.json (Ansible's base
# lock-down), so it carries the base URLBlocklist plus the console's rules.
CHROME_BASE = f"{CHROME_DIR}/cc-policy.json"
CHROME_URLS = f"{CHROME_DIR}/cc-urls.json"
EMPTY_POLICY = {"url_mode": "block_listed", "url_block": [], "url_allow": [], "egress_mode": "off",
                "egress_allow": [], "limits": {}, "assignments": []}


def ad_domain():
    if CONF.get("domain"):
        return CONF["domain"]
    try:
        for line in open("/etc/sssd/sssd.conf"):
            if line.strip().startswith("domains"):
                return line.split("=", 1)[1].split(",")[0].strip()
    except OSError:
        pass
    return None


DOMAIN = ad_domain()
events, events_lock = [], threading.Lock()
checkin_now = threading.Event()
_cache = {}
policy_state = {}                                # what was applied, reported in facts
policy_lock = threading.Lock()


def event(kind, message, user=None):
    with events_lock:
        events.append({"ts": time.time(), "kind": kind, "message": message, "user": user})


def sh(cmd, timeout=20, **kw):
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")


def cached(key, seconds, fn):
    t, v = _cache.get(key, (0, None))
    if time.time() - t > seconds:
        v = fn()
        _cache[key] = (time.time(), v)
    return v


def api(path, payload=None, timeout=30):
    req = urllib.request.Request(CONF["console_url"] + path, method="POST" if payload is not None else "GET",
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers={"Content-Type": "application/json", "X-CC-Device-Token": CONF["device_token"]})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


# ---- facts ------------------------------------------------------------------
_last_cpu = None


def cpu_percent():
    global _last_cpu
    vals = [int(x) for x in open("/proc/stat").readline().split()[1:]]
    idle, total = vals[3] + vals[4], sum(vals)
    pct = None
    if _last_cpu:
        di, dt = idle - _last_cpu[0], total - _last_cpu[1]
        pct = round(100 * (1 - di / dt), 1) if dt else 0.0
    _last_cpu = (idle, total)
    return pct


def meminfo():
    m = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
    total, avail = m["MemTotal"], m["MemAvailable"]
    return total // 1024, round(100 * (1 - avail / total), 1)


def os_name():
    for line in open("/etc/os-release"):
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return "Linux"


def is_container():
    return os.path.exists("/.dockerenv") or os.environ.get("container") == "docker" or \
        "docker" in open("/proc/1/environ", "rb").read().decode(errors="ignore")


def active(unit):
    return sh(["systemctl", "is-active", "--quiet", unit]).returncode == 0


def check(ok, detail="", severity="fail"):
    return {"ok": ok, "detail": detail, "severity": severity}


def ini_values(path):
    vals = {}
    try:
        for line in open(path):
            if "=" in line and not line.lstrip().startswith((";", "#", "[")):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    except OSError:
        pass
    return vals


def data_channels_blocked():
    """(ok, detail): nothing can leave an agent desktop over RDP - no clipboard,
    drives, printers or other device redirection, in either direction."""
    x, s = ini_values("/etc/xrdp/xrdp.ini"), ini_values("/etc/xrdp/sesman.ini")
    bad = [k for k in ("cliprdr", "rdpdr", "rail") if x.get(k, "true").lower() != "false"]
    bad += [k for k in ("RestrictOutboundClipboard", "RestrictInboundClipboard") if s.get(k, "none").lower() != "all"]
    return (not bad, "clipboard, drives and printers blocked" if not bad else "allowed: " + ", ".join(bad))


def compliance():
    c = {}
    joined = cached("adjoin", 300, lambda: sh(["adcli", "testjoin", "--domain", DOMAIN or ""]).returncode == 0)
    c["ad_joined"] = check(joined, "domain member" if joined else "not joined / trust broken")
    c["sssd"] = check(active("sssd"), "sssd running" if active("sssd") else "sssd stopped")
    sec = cached("updates", 3600, lambda: len([l for l in sh("apt-get -s -o Debug::NoLocking=1 upgrade",
                                                             timeout=120).stdout.splitlines()
                                               if l.startswith("Inst") and "security" in l]))
    c["security_updates"] = check(sec == 0, f"{sec} security updates pending", "warn")
    if ROLE == "session_host":
        up = active("xrdp") and active("xrdp-sesman")
        c["remote_desktop"] = check(up, "xrdp running" if up else "xrdp stopped")
        c["launch_relay"] = check(active("cc-launch-relay"), "open-on-laptop relay")
        c["per_user_caps"] = check(os.path.exists("/etc/systemd/system/user-.slice.d/50-cc-limits.conf"),
                                   "CPU/RAM cap per agent")
        c["kiosk_mode"] = check(os.path.exists("/etc/xdg/xfce4/kiosk/kioskrc"), "desktop locked for agents")
        c["no_data_out"] = check(*data_channels_blocked())
        term = "/usr/bin/xfce4-terminal"
        c["terminal_restricted"] = check(not os.path.exists(term) or os.stat(term).st_mode & 0o007 == 0,
                                         "terminal admins-only")
    elif ROLE == "broker":
        c["broker"] = check(active("cc-broker"), "SSO broker")
        c["console"] = check(active("cc-console"), "management console")
    elif ROLE == "endpoint":
        usb = "/bin/false" in sh("modprobe -n -v usb-storage").stdout
        c["usb_storage_blocked"] = check(usb, "USB storage blocked" if usb else "USB storage allowed")
        c["screen_lock"] = check(os.path.exists("/etc/dconf/db/local.d/locks/00-cc-lockdown"), "idle lock enforced")
        c["browser_policy"] = check(os.path.exists("/etc/opt/chrome/policies/managed/cc-policy.json"),
                                    "Chrome locked by policy")
        sudo = sh("grep -rlE '^%(domain|lab|cc)' /etc/sudoers /etc/sudoers.d/").stdout.strip()
        c["no_admin_rights"] = check(not sudo, "agents have no sudo" if not sudo else f"sudo rules: {sudo}")
        if os.path.exists("/etc/wireguard/wg0.conf"):
            hs = sh("wg show wg0 latest-handshakes").stdout.split()
            fresh = len(hs) >= 2 and hs[-1].isdigit() and time.time() - int(hs[-1]) < 300
            c["vpn"] = check(fresh, "tunnel up" if fresh else "no recent VPN handshake")
        if is_container():
            c["disk_encryption"] = check(None, "not applicable in the simulation")
        else:
            crypt = "crypt" in sh("lsblk -n -o TYPE").stdout.split()
            c["disk_encryption"] = check(crypt, "disk encrypted" if crypt else "disk not encrypted")
    if ROLE in POLICY_ROLES and policy_state:
        err = policy_state.get("error")
        c["policies"] = check(not err, err or f"policies v{policy_state['version']} applied")
    return c


def user_sessions():
    """Agent desktops on this session host: user, since, CPU %, RAM MB."""
    if ROLE != "session_host":
        return []
    out = sh("ps -eo user:32,pid,lstart,args --no-headers").stdout.splitlines()
    starts = {}
    for line in out:
        parts = line.split(None, 7)
        if len(parts) >= 8 and parts[7].startswith(("xfce4-session", "/usr/bin/xfce4-session")):
            starts.setdefault(parts[0], " ".join(parts[2:7]))
    usage = {}
    for line in sh("ps -eo user:32,rss,pcpu --no-headers").stdout.splitlines():
        u, rss, cpu = line.split()
        if u in starts:
            mb, pc = usage.get(u, (0, 0.0))
            usage[u] = (mb + int(rss) // 1024, pc + float(cpu))
    return [{"user": u, "since": s, "mem_mb": usage.get(u, (0, 0))[0], "cpu": round(usage.get(u, (0, 0))[1], 1)}
            for u, s in sorted(starts.items())]


def facts():
    mem_mb, mem_pct = meminfo()
    st = os.statvfs("/")
    return {
        "os": os_name(), "kernel": os.uname().release, "agent": VERSION,
        "uptime_s": int(float(open("/proc/uptime").read().split()[0])),
        "cpus": os.cpu_count(), "mem_mb": mem_mb,
        "metrics": {"cpu": cpu_percent() or 0.0, "mem_pct": mem_pct, "load": round(os.getloadavg()[0], 2),
                    "disk_pct": round(100 * (1 - st.f_bavail / st.f_blocks), 1)},
        "simulated": is_container(),
        **({"policy": policy_state} if ROLE in POLICY_ROLES and policy_state else {}),
    }


# ---- desktop catalog (session hosts) -----------------------------------------
def apply_catalog(cat, force=False):
    try:
        current = json.load(open(CATALOG)).get("version")
    except (OSError, ValueError):
        current = None
    if not force and current == cat["version"]:
        return
    os.makedirs(os.path.dirname(CATALOG), exist_ok=True)
    with open(CATALOG + ".tmp", "w") as f:
        json.dump(cat, f, indent=2)
    os.replace(CATALOG + ".tmp", CATALOG)
    sh(["/usr/local/sbin/cc-desktop-render"])
    for s in user_sessions():                     # refresh icons on desktops already open
        run_as_user(s["user"], "/usr/local/bin/cc-desktop-setup")
    event("desktop", f"desktop catalog v{cat['version']} applied ({len(cat['items'])} systems)")
    saved = load_json(POLICY)
    if saved and saved["policy"]["url_mode"] == "allow_only":
        apply_policy(saved, force=True)           # the allow-list includes the catalog's systems


# ---- policies: browser URL rules and outbound firewall ------------------------
def load_json(path):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return None


def write_if_changed(path, text, mode=0o644):
    """True if the file was (re)written."""
    try:
        if open(path).read() == text:
            return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(text)
    os.chmod(path + ".tmp", mode)
    os.replace(path + ".tmp", path)
    return True


def chrome_url_policy(pol):
    """URLBlocklist/URLAllowlist for cc-urls.json. allow_only blocks everything
    except the listed sites, the desktop's own systems and Genesys."""
    base = load_json(CHROME_BASE) or {}
    if pol["url_mode"] == "allow_only":
        catalog = [it["url"] for it in (load_json(CATALOG) or {}).get("items", [])]
        genesys = [p.replace("[*.]", "") for p in base.get("AudioCaptureAllowedUrls", [])]
        block = ["*"]
        allow = pol["url_allow"] + catalog + genesys + ["chrome://newtab", "about:blank"]
    else:
        block = base.get("URLBlocklist", []) + pol["url_block"]
        allow = pol["url_allow"]
    return {"URLBlocklist": list(dict.fromkeys(block)), "URLAllowlist": list(dict.fromkeys(allow))}


def agent_uids_running():
    """uids (>= 1000, not nobody) that have processes on this machine now."""
    out = sh("ps -eo uid= | sort -u").stdout.split()
    return {int(u) for u in out if u.isdigit() and int(u) >= 1000 and int(u) != 65534}


def resolve_assignments(pol):
    """uid -> index into pol['assignments']. User targets beat group targets;
    among groups the first listed wins. Group members come from the directory
    (SSSD) and, for users signed in now, from their full group list, so nested
    and primary groups count too."""
    assignments = pol.get("assignments", [])
    running = agent_uids_running()
    owner = {}
    for kind in ("user", "group"):
        for i, a in enumerate(assignments):
            t_kind, name = a["target"].split(":", 1)
            if t_kind != kind:
                continue
            uids = set()
            if kind == "user":
                try:
                    uids.add(pwd.getpwnam(name).pw_uid)
                except KeyError:
                    pass
            else:
                try:
                    g = grp.getgrnam(name)
                except KeyError:
                    continue
                for m in g.gr_mem:
                    try:
                        uids.add(pwd.getpwnam(m).pw_uid)
                    except KeyError:
                        pass
                for uid in running:
                    try:
                        pw = pwd.getpwuid(uid)
                        if g.gr_gid in os.getgrouplist(pw.pw_name, pw.pw_gid):
                            uids.add(uid)
                    except KeyError:
                        pass
            for uid in uids:
                if uid >= 1000 and uid != 65534:
                    owner.setdefault(uid, i)
    return owner


def _rule_lines(rules):
    out = []
    for r in rules:
        fam = "ip6" if ":" in r["dest"] else "ip"
        match = f"{r['proto']} dport {{ {r['ports'].replace(',', ', ')} }}" if r["ports"] else f"meta l4proto {r['proto']}"
        name = re.sub(r"[^A-Za-z0-9 ._-]", "", r.get("name") or "")[:60]
        out.append(f'    {fam} daddr {r["dest"]} {match} accept comment "{name or r["dest"]}"')
    return out


REJECT = ['    limit rate 10/minute log prefix "cc-policy blocked: "',
          "    meta l4proto tcp reject with tcp reset",
          "    reject"]


def nft_ruleset(pol, owner):
    """Outbound allow-list for sockets owned by agent users (uid >= 1000, not
    nobody). Root, system services and kernel traffic (NFS, sssd) are untouched,
    so a bad rule can cut off an agent's apps but never the machine itself.
    Users with a group/user assignment go to that assignment's chain first."""
    out = ["table inet cc_policy", "delete table inet cc_policy"]   # idempotent replace
    assignments = pol.get("assignments", [])
    used = sorted(set(owner.values()))
    needed = pol["egress_mode"] == "allow_only" or any(assignments[i]["egress_mode"] == "allow_only" for i in used)
    if not needed:
        return "\n".join(out) + "\n"
    out += ["table inet cc_policy {",
            "  chain output {",
            "    type filter hook output priority 0; policy accept;",
            "    meta skuid >= 1000 meta skuid != 65534 jump agent",
            "  }",
            "  chain agent {",
            '    oif "lo" accept',
            "    ct state established,related accept"]
    for i in used:
        uids = ", ".join(str(u) for u in sorted(u for u, j in owner.items() if j == i))
        out.append(f"    meta skuid {{ {uids} }} goto assign_{i}")
    out += ["    goto everyone", "  }",
            "  chain everyone {"]
    out += _rule_lines(pol["egress_allow"]) + REJECT if pol["egress_mode"] == "allow_only" else ["    accept"]
    out.append("  }")
    for i in used:
        a = assignments[i]
        out.append(f"  chain assign_{i} {{")
        if a["egress_mode"] == "off":
            out.append("    accept")
        elif a["egress_mode"] == "allow_only":
            out += _rule_lines(a["egress_allow"]) + REJECT
        else:                                    # inherit: extra destinations, then the everyone rules
            out += _rule_lines(a["egress_allow"]) + ["    goto everyone"]
        out.append("  }")
    out.append("}")
    return "\n".join(out) + "\n"


SLICE_DROPIN = "60-cc-policy.conf"


def slice_conf(limits):
    lines = []
    if limits.get("cpu_pct"):
        lines.append(f"CPUQuota={limits['cpu_pct']}%")
    if limits.get("mem_mb"):
        lines.append(f"MemoryMax={limits['mem_mb']}M")
    return "# Written by cc-device-agent from the console's policies.\n[Slice]\n" + "\n".join(lines) + "\n" if lines else None


def apply_limits(pol, owner):
    """Per-user CPU/RAM caps on session hosts as systemd user-slice drop-ins:
    the policy's limits for everyone (user-.slice.d), an assignment's limits for
    its users (user-<uid>.slice.d). 0 keeps the server's own default."""
    base = pol.get("limits") or {}
    want = {"/etc/systemd/system/user-.slice.d/" + SLICE_DROPIN: slice_conf(base)}
    for uid, i in owner.items():
        a_lim = pol["assignments"][i].get("limits") or {}
        if a_lim.get("cpu_pct") or a_lim.get("mem_mb"):
            merged = {k: a_lim.get(k) or base.get(k) for k in ("cpu_pct", "mem_mb")}
            want[f"/etc/systemd/system/user-{uid}.slice.d/{SLICE_DROPIN}"] = slice_conf(merged)
    changed = False
    for d in os.listdir("/etc/systemd/system"):
        path = f"/etc/systemd/system/{d}/{SLICE_DROPIN}"
        if d.startswith("user-") and d.endswith(".slice.d") and os.path.exists(path) and not want.get(path):
            os.remove(path)
            changed = True
    for path, text in want.items():
        if text:
            changed |= write_if_changed(path, text)
    if changed:
        running = agent_uids_running()
        for uid in running:                      # our earlier --runtime values would shadow /etc
            for prop in ("CPUQuota", "MemoryMax"):
                try:
                    os.remove(f"/run/systemd/system.control/user-{uid}.slice.d/50-{prop}.conf")
                except OSError:
                    pass
        sh(["systemctl", "daemon-reload"])
        for uid in running:                      # re-apply to desktops already running
            props = sh(["systemctl", "show", f"user-{uid}.slice", "-p", "CPUQuotaPerSecUSec", "-p", "MemoryMax"]).stdout
            vals = dict(l.split("=", 1) for l in props.splitlines() if "=" in l)
            args = []
            if vals.get("CPUQuotaPerSecUSec", "infinity") != "infinity":
                secs = vals["CPUQuotaPerSecUSec"].rstrip("s")
                args.append(f"CPUQuota={round(float(secs.rstrip('m')) / (1000 if secs.endswith('m') else 1) * 100)}%")
            if vals.get("MemoryMax", "infinity") != "infinity":
                args.append(f"MemoryMax={vals['MemoryMax']}")
            if args:
                sh(["systemctl", "set-property", "--runtime", f"user-{uid}.slice"] + args)
    return changed


def apply_policy(entry, force=False):
    """entry = {"version": n, "policy": {...}} from the console. Runs on every
    check-in (cheap: files and rules are only rewritten when they change), so a
    user who signs in later gets their group's rules within one check-in."""
    global policy_state
    pol = dict(EMPTY_POLICY, **entry["policy"])
    with policy_lock:
        errors = []
        if os.path.isdir(CHROME_DIR):
            try:
                write_if_changed(CHROME_URLS, json.dumps(chrome_url_policy(pol), indent=4, sort_keys=True) + "\n")
            except OSError as e:
                errors.append(f"browser: {e}")
        owner = resolve_assignments(pol)
        rules = nft_ruleset(pol, owner)
        if rules.count("\n") > 2 or os.path.exists(NFT_RULES):
            if write_if_changed(NFT_RULES, rules, 0o600) or force or policy_state.get("error"):
                r = sh(["nft", "-f", NFT_RULES])
                if r.returncode != 0:
                    errors.append("firewall: " + (r.stderr or r.stdout or "nft failed").strip()[-300:])
        if ROLE == "session_host":
            try:
                apply_limits(pol, owner)
            except OSError as e:
                errors.append(f"limits: {e}")
        write_if_changed(POLICY, json.dumps(entry, indent=2) + "\n", 0o600)
        changed = policy_state.get("version") != entry["version"]
        counts = {}
        for i in owner.values():
            t = pol["assignments"][i]["target"]
            counts[t] = counts.get(t, 0) + 1
        policy_state = {"version": entry["version"], "url_mode": pol["url_mode"],
                        "url_rules": len(pol["url_block"]) + len(pol["url_allow"]),
                        "egress_mode": pol["egress_mode"], "egress_rules": len(pol["egress_allow"]),
                        "limits": pol.get("limits"), "assigned_users": counts,
                        "applied_at": time.time(), "error": "; ".join(errors) or None}
    if errors and (changed or force):
        event("policy", f"policies v{entry['version']} NOT applied: {policy_state['error']}")
    elif changed or force:
        event("policy", f"policies v{entry['version']} applied (URLs: {pol['url_mode']}, firewall: {pol['egress_mode']}, "
                        f"{len(pol['assignments'])} group/user assignments)")


# ---- acting inside an agent's desktop session --------------------------------
def session_env(user):
    """DISPLAY / XAUTHORITY / D-Bus address of user's desktop, from its xfce4-session."""
    r = sh(["pgrep", "-o", "-u", user, "-x", "xfce4-session"])
    if r.returncode != 0:
        return None
    raw = open(f"/proc/{r.stdout.strip()}/environ", "rb").read().split(b"\0")
    env = dict(x.decode(errors="ignore").split("=", 1) for x in raw if b"=" in x)
    home = pwd.getpwnam(user).pw_dir
    return {"DISPLAY": env.get("DISPLAY", ":10"), "XAUTHORITY": env.get("XAUTHORITY", f"{home}/.Xauthority"),
            "DBUS_SESSION_BUS_ADDRESS": env.get("DBUS_SESSION_BUS_ADDRESS", ""), "HOME": home}


def run_as_user(user, cmd, timeout=20):
    env = session_env(user)
    if not env:
        return None
    args = ["runuser", "-u", user, "--", "env"] + [f"{k}={v}" for k, v in env.items()] + ["sh", "-c", cmd]
    return sh(args, timeout=timeout)


def notify(user, title, text, urgent=True):
    """Desktop notification in the agent's session. Messages stay until dismissed;
    the "being viewed" notice disappears after a few seconds."""
    opts = "-u critical" if urgent else "-u normal -t 8000"
    r = run_as_user(user, f"notify-send {opts} -a 'IT support' {sh_quote(title)} {sh_quote(text)}")
    return r is not None and r.returncode == 0


def sh_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


# ---- actions ---------------------------------------------------------------
def do(action, args):
    """Returns (ok, output, png_bytes_or_None)."""
    if action == "refresh":
        checkin_now.set()
        return True, "check-in requested", None
    if action == "message":
        users = [args["user"]] if args.get("user") else [s["user"] for s in user_sessions()]
        sent = [u for u in users if notify(u, "Message from IT", args["text"])]
        return bool(sent) or not users, f"delivered to {', '.join(sent) or 'nobody (no sessions)'}", None
    if action == "screenshot_user":
        user = args["user"]
        env = session_env(user)
        if not env:
            return False, f"{user} has no desktop on this host", None
        if args.get("first") == "1" and args.get("notify", "1") == "1":
            notify(user, "IT support", "An administrator is viewing your screen.", urgent=False)
        r = subprocess.run(["import", "-window", "root", "-display", env["DISPLAY"], "-resize", "1280x>", "png:-"],
                           capture_output=True, env={"XAUTHORITY": env["XAUTHORITY"], "PATH": "/usr/bin:/bin"},
                           timeout=20)
        if r.returncode != 0:
            return False, r.stderr.decode(errors="ignore")[-500:], None
        return True, f"screen of {user}", r.stdout
    if action == "logoff_user":
        user = args["user"]
        sh(["pkill", "-TERM", "-u", user])
        time.sleep(3)
        sh(["pkill", "-KILL", "-u", user])
        event("session", f"{user} logged off by an administrator", user)
        return True, f"{user} logged off", None
    if action == "collect_logs":
        units = {"session_host": "-u xrdp -u xrdp-sesman -u sssd -u cc-launch-relay",
                 "broker": "-u cc-broker -u cc-console -u sssd", "endpoint": "-u sssd -u wg-quick@wg0"}[ROLE]
        return True, sh(f"journalctl {units} -n 300 --no-pager").stdout[-60000:], None
    if action == "restart_service":
        allowed = {"session_host": {"xrdp", "xrdp-sesman", "sssd", "cc-launch-relay"},
                   "broker": {"cc-broker", "sssd"}}[ROLE]
        svc = args["service"]
        if svc not in allowed:
            return False, f"{svc} is not one of {sorted(allowed)}", None
        r = sh(["systemctl", "restart", svc])
        event("service", f"{svc} restarted by an administrator")
        return r.returncode == 0, r.stderr or f"{svc} restarted", None
    if action == "apply_catalog":
        cat = api("/agent/checkin", payload(), timeout=30).get("catalog")
        if cat:
            apply_catalog(cat, force=True)
        return True, "desktop re-applied", None
    if action == "apply_policy":
        pol = api("/agent/checkin", payload(), timeout=30).get("policy")
        if not pol:
            return False, "no policy for this device", None
        apply_policy(pol, force=True)
        return not policy_state.get("error"), policy_state.get("error") or f"policies v{pol['version']} applied", None
    if action == "reboot":
        event("power", "reboot requested by an administrator")
        threading.Timer(3, lambda: sh(["systemctl", "reboot"])).start()
        return True, "rebooting in 3 s", None
    return False, f"unknown action {action}", None


def run_command(cmd):
    try:
        ok, out, png = do(cmd["action"], cmd.get("args", {}))
    except Exception as e:                       # report, never crash the agent
        ok, out, png = False, f"{type(e).__name__}: {e}", None
    res = {"id": cmd["id"], "ok": ok, "output": out}
    if png:
        res["image"] = base64.b64encode(png).decode()
    try:
        api("/agent/result", res)
    except Exception as e:
        print(f"cannot report result: {e}", flush=True)


def payload():
    with events_lock:
        ev = events[:]
        events.clear()
    return {"device": DEVICE, "role": ROLE, "facts": facts(), "compliance": compliance(),
            "sessions": user_sessions(), "events": ev}


def checkin_loop():
    known_sessions = set()
    while True:
        try:
            p = payload()
            now_sessions = {s["user"] for s in p["sessions"]}
            for u in now_sessions - known_sessions:
                p["events"].append({"ts": time.time(), "kind": "session", "message": f"{u} started a desktop", "user": u})
            for u in known_sessions - now_sessions:
                p["events"].append({"ts": time.time(), "kind": "session", "message": f"{u} desktop ended", "user": u})
            known_sessions = now_sessions
            reply = api("/agent/checkin", p)
            for cmd in reply.get("commands", []):
                threading.Thread(target=run_command, args=(cmd,), daemon=True).start()
            if ROLE == "session_host" and reply.get("catalog"):
                apply_catalog(reply["catalog"])
            if ROLE in POLICY_ROLES and reply.get("policy"):
                apply_policy(reply["policy"])
        except Exception as e:
            print(f"check-in failed: {e}", flush=True)
        checkin_now.wait(15)
        checkin_now.clear()


def wait_loop():
    while True:
        try:
            for cmd in api(f"/agent/wait?device={DEVICE}&timeout=25", timeout=40).get("commands", []):
                threading.Thread(target=run_command, args=(cmd,), daemon=True).start()
        except Exception as e:
            print(f"wait failed: {e}", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    event("agent", f"agent {VERSION} started")
    if ROLE in POLICY_ROLES and load_json(POLICY):
        apply_policy(load_json(POLICY), force=True)   # firewall rules do not survive a reboot
    threading.Thread(target=wait_loop, daemon=True).start()
    checkin_loop()
