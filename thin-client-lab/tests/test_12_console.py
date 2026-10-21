"""Management console: sign-in rules, device check-ins and compliance, remote
actions (live screen of an agent desktop and of the laptop, message, start app,
log off), allow-listed actions only, catalog rings with promote and rollback,
audit log and device timeline."""
import time

import pytest
import requests

from conftest import CFG
from lib import end_desktops, session_host_of, wait_for
from test_11_viewer import as_user

pytestmark = pytest.mark.skipif("console" not in CFG, reason="no console in targets")
PNG = b"\x89PNG\r\n\x1a\n"


class Console:
    def __init__(self, url):
        self.url, self.s = url, requests.Session()
        self.s.headers["X-CC-Console"] = "1"

    def login(self, user, password):
        return self.s.post(f"{self.url}/api/login", json={"username": user, "password": password}, timeout=20)

    def get(self, path):
        r = self.s.get(self.url + path, timeout=20)
        r.raise_for_status()
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content

    def post(self, path, body=None):
        r = self.s.post(self.url + path, json=body or {}, timeout=20)
        r.raise_for_status()
        return r.json()

    def act(self, device, action, **args):
        """Queue an action like the dashboard buttons do; return (command, image bytes)."""
        cid = self.post(f"/api/devices/{device}/actions", {"action": action, "args": args})["id"]
        cmd = wait_for(lambda: (c := self.get(f"/api/commands/{cid}"))["status"] in ("done", "failed", "expired") and c,
                       45, interval=0.5, what=f"{action} on {device}")
        img = self.get(f"/api/commands/{cid}/image") if cmd.get("has_image") else b""
        return cmd, img


def signed_in(cfg, who):
    con = Console(cfg["console"]["url"])
    a = cfg["console"][who]
    assert con.login(a["username"], a["password"]).status_code == 200, who
    return con


@pytest.fixture(scope="module")
def c(cfg):
    return signed_in(cfg, "admin")


@pytest.fixture(scope="module")
def c2(cfg):
    """A second admin, for the two-person rule."""
    return signed_in(cfg, "admin2")


def test_console_and_agent_running(lab, cfg):
    h = lab.host(cfg["console"]["host"])
    for svc in ("cc-console", "cc-device-agent"):
        assert h.service(svc).is_running and h.service(svc).is_enabled


def test_only_ad_it_staff_can_sign_in(cfg):
    con = Console(cfg["console"]["url"])
    agent = cfg["users"]["agent"]
    assert con.login(agent["username"], agent["password"]).status_code == 403, "agent is in no IT group"
    assert con.login(cfg["console"]["admin"]["username"], "wrong-password").status_code == 403
    assert requests.get(cfg["console"]["url"] + "/api/devices", timeout=10).status_code == 401


def test_requests_without_csrf_header_refused(cfg, c):
    r = c.s.post(f"{c.url}/api/catalog/promote", json={}, headers={"X-CC-Console": ""}, timeout=10)
    assert r.status_code == 403


def test_devices_need_a_valid_token_or_kerberos(cfg):
    url = cfg["console"]["url"] + "/agent/checkin"
    assert requests.post(url, json={"device": "evil"}, headers={"X-CC-Device-Token": "nope"}, timeout=10).status_code == 403
    r = requests.post(url, json={"device": "evil"}, timeout=10)
    assert r.status_code == 401 and r.headers.get("WWW-Authenticate") == "Negotiate"


def _machines(cfg):
    return cfg["session_hosts"] + cfg["office_endpoints"] + cfg["home_endpoints"] + [cfg["console"]["host"]]


def test_every_machine_reports_and_is_online(cfg, c):
    want = set(_machines(cfg))
    online = lambda: want <= {d["id"] for d in c.get("/api/devices") if d["online"]} or None
    wait_for(online, 60, what=f"{sorted(want)} online in the console")


def test_servers_are_compliant(cfg, c):
    devs = {d["id"]: d for d in c.get("/api/devices")}
    for name in cfg["session_hosts"] + [cfg["console"]["host"]]:
        d = devs[name]
        failed = [k for k, v in d["compliance"].items() if v["ok"] is False and v.get("severity", "fail") == "fail"]
        assert not failed, f"{name}: {failed}"
        assert d["compliance"]["ad_joined"]["ok"] is True


@pytest.fixture(scope="module")
def agent_on_laptop(lab, cfg):
    """agent1 logs in to the (simulated Windows) laptop, which reports to the console."""
    u = cfg["users"]["agent"]
    laptop = lab.host(cfg["viewer"]["laptop"])
    end_desktops(lab, u["username"])
    laptop.run(f"pkill -u {u['username']}; rm -f /tmp/genesys-desktop-{u['username']}.log")
    as_user(laptop, u["username"], u["password"], f"nohup setsid cc-laptop-session > /tmp/laptop-{u['username']}.log 2>&1 &")
    host = wait_for(lambda: session_host_of(lab, u["username"]), 60, what="agent desktop")
    yield u["username"], host
    laptop.run(f"pkill -u {u['username']}")
    end_desktops(lab, u["username"])


def test_sessions_link_agent_desktop_to_laptop(cfg, c, agent_on_laptop):
    user, host = agent_on_laptop
    lap = cfg["console"]["laptop_device"]

    def linked():
        return next((s for s in c.get("/api/sessions") if s["user"] == user and s["laptop"] == lap and s["genesys"]), None)
    s = wait_for(linked, 60, what="session with laptop and Genesys in the console")
    assert s["host"] == host


def test_watch_agent_desktop(cfg, c, agent_on_laptop):
    user, host = agent_on_laptop
    wait_for(lambda: any(s["user"] == user for s in c.get(f"/api/devices/{host}")["sessions"]), 40, what="session reported")
    cmd, img = c.act(host, "screenshot_user", user=user, first="1")
    assert cmd["status"] == "done" and img.startswith(PNG), cmd
    assert any(a["action"] == "screenshot_user" and a["target"] == host for a in c.get("/api/audit")), "view not audited"


def test_view_laptop_screen(cfg, c, agent_on_laptop):
    cmd, img = c.act(cfg["console"]["laptop_device"], "screenshot", first="1")
    assert cmd["status"] == "done" and img.startswith(PNG), cmd


def test_message_and_start_genesys(lab, cfg, c, agent_on_laptop):
    user, host = agent_on_laptop
    cmd, _ = c.act(host, "message", user=user, text="Test message from IT")
    assert cmd["status"] == "done" and user in cmd["result"]
    laptop = lab.host(cfg["viewer"]["laptop"])
    before = laptop.run(f"cat /tmp/genesys-desktop-{user}.log").stdout.count("focus")
    cmd, _ = c.act(cfg["console"]["laptop_device"], "start_app", app="genesys")
    assert cmd["status"] == "done"
    wait_for(lambda: laptop.run(f"cat /tmp/genesys-desktop-{user}.log").stdout.count("focus") > before, 15,
             what="Genesys brought to front on the laptop")


def test_device_timeline_shows_what_happened(cfg, c, agent_on_laptop):
    events = [e["message"] for e in c.get(f"/api/devices/{cfg['console']['laptop_device']}")["events"]]
    assert any("remote desktop opened" in m for m in events), events
    assert any("starting genesys" in m for m in events), events


def test_only_allow_listed_actions(cfg, c):
    host = cfg["session_hosts"][0]
    r = c.s.post(f"{c.url}/api/devices/{host}/actions", json={"action": "shell", "args": {"cmd": "id"}}, timeout=10)
    assert r.status_code == 400
    cmd, _ = c.act(host, "restart_service", service="ssh")
    assert cmd["status"] == "failed" and "not one of" in cmd["result"]


def test_log_user_off(lab, cfg, c, agent_on_laptop):
    user, host = agent_on_laptop
    cmd, _ = c.act(host, "logoff_user", user=user)
    assert cmd["status"] == "done"
    wait_for(lambda: session_host_of(lab, user) is None, 30, what="desktop ended")


@pytest.mark.slow
def test_catalog_rings_promote_and_rollback(lab, cfg, c, c2):
    pilot, broad = cfg["session_hosts"][0], cfg["session_hosts"][1]
    cat = c.get("/api/catalog")
    start = cat["rings"]["broad"]
    items = next(v for v in cat["versions"] if v["version"] == start)["items"]
    icon = "/usr/share/cc-desktop/cc-web-test-wallboard.desktop"
    has = lambda h: lab.host(h).file(icon).exists
    c.post(f"/api/devices/{pilot}/ring", {"ring": "pilot"})
    try:
        c.post("/api/catalog", {"items": items + [{"name": "Test Wallboard", "url": "http://crm.lab.example.com/",
                                                   "icon": "utilities-system-monitor"}]})
        wait_for(lambda: has(pilot), 45, what="new icon on the pilot host")
        time.sleep(20)
        assert not has(broad), "broad ring changed before promotion"
        c2.post("/api/catalog/promote")                 # a second admin promotes
        wait_for(lambda: has(broad), 45, what="new icon on the broad host after promotion")
        policy = lab.host(broad).file("/etc/opt/chrome/policies/managed/cc-desktop.json").content_string
        assert "Test Wallboard" in policy
    finally:
        c.post("/api/catalog/rollback", {"version": start})
        c.post(f"/api/devices/{pilot}/ring", {"ring": "broad"})
    wait_for(lambda: not has(pilot) and not has(broad), 45, what="rollback on every host")


def test_it_roles_are_enforced_by_the_server(cfg, c):
    """Viewers only read; helpdesk does support actions but not reboots, log-offs
    or configuration; every refusal is audited."""
    viewer, helpdesk = signed_in(cfg, "viewer"), signed_in(cfg, "helpdesk")
    host = cfg["session_hosts"][0]
    assert viewer.get("/api/me")["role"] == "viewer" and helpdesk.get("/api/me")["role"] == "helpdesk"
    assert any(d["id"] == host for d in viewer.get("/api/devices")), "viewers can read"

    def act(con, action, **args):
        return con.s.post(f"{con.url}/api/devices/{host}/actions", json={"action": action, "args": args}, timeout=10).status_code
    assert act(viewer, "refresh") == 403
    assert act(helpdesk, "refresh") == 200
    assert act(helpdesk, "reboot") == 403
    assert act(helpdesk, "logoff_user", user="nobody") == 403
    for con in (viewer, helpdesk):
        assert con.s.post(f"{con.url}/api/policy", json={}, timeout=10).status_code == 403
        assert con.s.post(f"{con.url}/api/catalog/rollback", json={"version": 1}, timeout=10).status_code == 403
        assert con.s.post(f"{con.url}/api/devices/{host}/ring", json={"ring": "pilot"}, timeout=10).status_code == 403
    assert "reboot" not in helpdesk.get(f"/api/devices/{host}")["actions"], "UI only offers allowed actions"
    assert viewer.get(f"/api/devices/{host}")["actions"] == {}
    denied = [a for a in c.get("/api/audit") if a["action"] == "denied"]
    assert any(a["admin"] == cfg["console"]["helpdesk"]["username"] for a in denied), "refusals audited"


def test_two_person_rule_for_promotion(cfg, c, c2):
    cat = c.get("/api/catalog")
    start = cat["rings"]["broad"]
    items = next(v for v in cat["versions"] if v["version"] == start)["items"]
    try:
        c.post("/api/catalog", {"items": items, "note": "two-person test"})
        r = c.s.post(f"{c.url}/api/catalog/promote", json={}, timeout=10)
        assert r.status_code == 403 and "second admin" in r.json()["error"]
        assert c2.post("/api/catalog/promote")["version"] > start
    finally:
        c.post("/api/catalog/rollback", {"version": start})
