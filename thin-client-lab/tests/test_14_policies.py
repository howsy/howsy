"""Console policies: browser URL rules and the outbound firewall for agent users,
validated by the console, published to the pilot ring first, promoted, rolled
back. The firewall only filters agent users' traffic: root and system services
(SSSD, NFS) keep working whatever the rules say."""
import json

import pytest

from conftest import CFG
from lib import q, wait_for
from test_12_console import Console, signed_in

pytestmark = pytest.mark.skipif("console" not in CFG, reason="no console in targets")
URLS = "/etc/opt/chrome/policies/managed/cc-urls.json"
DC_LDAP = ("10.10.0.10", 389)


@pytest.fixture(scope="module")
def c(cfg):
    con = Console(cfg["console"]["url"])
    a = cfg["console"]["admin"]
    assert con.login(a["username"], a["password"]).status_code == 200
    return con


def tcp_as(host, user, ip, port):
    """True if `user` (not root) can open a TCP connection from host to ip:port."""
    script = ("import socket,sys\ns=socket.socket(); s.settimeout(4)\n"
              f"try:\n    s.connect(({ip!r},{port})); print('open')\nexcept Exception as e:\n    print(e); sys.exit(1)\n")
    prefix = f"runuser -u {q(user)} -- " if user != "root" else ""
    return host.run(f"{prefix}python3 -c {q(script)}").rc == 0


def url_rules(host):
    f = host.file(URLS)
    return json.loads(f.content_string) if f.exists else {}


def test_policy_is_validated(c):
    def publish(body):
        return c.s.post(f"{c.url}/api/policy", json=body, timeout=10)
    assert publish({"egress_mode": "allow_only", "egress_allow": []}).status_code == 400, "allow-only with no rules"
    assert publish({"egress_mode": "allow_only",
                    "egress_allow": [{"dest": "crm.example.com", "proto": "tcp", "ports": "80"}]}).status_code == 400
    assert publish({"egress_mode": "allow_only",
                    "egress_allow": [{"dest": "10.0.0.0/8", "proto": "tcp", "ports": "99999"}]}).status_code == 400
    assert publish({"url_block": ["bad url with spaces"]}).status_code == 400
    assert publish({"url_mode": "everything"}).status_code == 400
    r = c.s.post(f"{c.url}/api/policy", json={}, headers={"X-CC-Console": ""}, timeout=10)
    assert r.status_code == 403, "CSRF header required"


def test_devices_report_their_policy(cfg, c):
    want = set(cfg["session_hosts"] + cfg["office_endpoints"])
    reported = lambda: want <= {d["id"] for d in c.get("/api/policy")["devices"] if d["applied"]} or None
    wait_for(reported, 60, what="policy version reported by session hosts and endpoints")


@pytest.mark.slow
def test_url_and_firewall_policy_by_ring(lab, cfg, c):
    pilot_name, broad_name = cfg["session_hosts"][0], cfg["session_hosts"][1]
    pilot, broad = lab.host(pilot_name), lab.host(broad_name)
    agent = cfg["users"]["agent"]["username"]
    crm_ip = pilot.check_output("getent hosts crm.lab.example.com | awk '{print $1}'")
    assert tcp_as(pilot, agent, *DC_LDAP), "agent can reach LDAP before any firewall policy"

    start = c.get("/api/policy")["rings"]["broad"]
    c.post(f"/api/devices/{pilot_name}/ring", {"ring": "pilot"})
    try:
        v = c.post("/api/policy", {
            "url_mode": "block_listed", "url_block": ["blocked.example.com"], "url_allow": [],
            "egress_mode": "allow_only",
            "egress_allow": [{"name": "CRM web", "dest": f"{crm_ip}/32", "proto": "tcp", "ports": "80"}],
        })["version"]

        # Pilot gets it: URL rule on top of the base lock-down, firewall for agent users only.
        wait_for(lambda: "blocked.example.com" in url_rules(pilot).get("URLBlocklist", []), 45,
                 what="URL rule on the pilot host")
        assert "file://*" in url_rules(pilot)["URLBlocklist"], "base lock-down kept"
        wait_for(lambda: not tcp_as(pilot, agent, *DC_LDAP), 30, what="agent blocked from LDAP on the pilot host")
        assert tcp_as(pilot, agent, crm_ip, 80), "allowed destination still reachable"
        assert tcp_as(pilot, "root", *DC_LDAP), "root/system traffic is never filtered"
        assert pilot.run(f"id {q(agent)}").rc == 0, "SSSD lookups still work"
        wait_for(lambda: {d["id"]: d["applied"] for d in c.get("/api/policy")["devices"]}[pilot_name]["version"] == v,
                 30, what="pilot reports the new version")

        # Broad ring is untouched until promotion.
        assert "blocked.example.com" not in url_rules(broad).get("URLBlocklist", [])
        assert tcp_as(broad, agent, *DC_LDAP), "broad ring changed before promotion"
        signed_in(cfg, "admin2").post("/api/policy/promote")   # two-person rule
        wait_for(lambda: not tcp_as(broad, agent, *DC_LDAP), 45, what="firewall on the broad host after promotion")
        applied = {d["id"]: d["applied"] for d in c.get("/api/policy")["devices"]}
        assert not applied[broad_name].get("error"), applied[broad_name]
    finally:
        c.post("/api/policy/rollback", {"version": start})
        c.post(f"/api/devices/{pilot_name}/ring", {"ring": "broad"})
    wait_for(lambda: tcp_as(pilot, agent, *DC_LDAP) and tcp_as(broad, agent, *DC_LDAP), 45,
             what="firewall removed everywhere after rollback")
    assert "blocked.example.com" not in url_rules(pilot).get("URLBlocklist", [])
    assert any(a["action"] == "published policies" for a in c.get("/api/audit")), "publish audited"


def test_allow_only_urls_keep_desktop_systems_and_genesys(lab, cfg, c):
    host = lab.host(cfg["session_hosts"][0])
    start = c.get("/api/policy")["rings"]["broad"]
    c.post(f"/api/devices/{cfg['session_hosts'][0]}/ring", {"ring": "pilot"})
    try:
        c.post("/api/policy", {"url_mode": "allow_only", "url_allow": ["intranet.example.com"]})
        rules = wait_for(lambda: (r := url_rules(host)).get("URLBlocklist") == ["*"] and r, 45,
                         what="allow-only URL rules")
        allow = rules["URLAllowlist"]
        assert "intranet.example.com" in allow
        catalog = json.loads(host.file("/etc/cc-desktop/catalog.json").content_string)["items"]
        assert all(it["url"] in allow for it in catalog), "every desktop system stays reachable"
        assert any("mypurecloud" in u for u in allow), "Genesys stays reachable"
    finally:
        c.post("/api/policy/rollback", {"version": start})
        c.post(f"/api/devices/{cfg['session_hosts'][0]}/ring", {"ring": "broad"})
    wait_for(lambda: url_rules(host).get("URLBlocklist") != ["*"], 45, what="rollback")


def test_assignments_are_validated(c):
    def publish(body):
        return c.s.post(f"{c.url}/api/policy", json=body, timeout=10)
    assert publish({"assignments": [{"target": "everyone"}]}).status_code == 400
    assert publish({"assignments": [{"target": "group:cc-agents-test"}, {"target": "GROUP:CC-Agents-Test"}]}).status_code == 400
    assert publish({"assignments": [{"target": "user:agent2", "egress_mode": "allow_only"}]}).status_code == 400
    assert publish({"limits": {"mem_mb": 10}}).status_code == 400


def slice_value(host, uid, prop):
    return host.check_output(f"systemctl show user-{uid}.slice -p {prop} --value")


@pytest.mark.slow
def test_group_and_user_assignments(lab, cfg, c):
    """Everyone: no firewall, 3 GB each. Group CC-Agents-Test: 1.5 GB and 1.5 cores.
    User agent2 (also in that group): allow-only to the CRM; a user assignment
    beats the group one, so agent2 gets everyone's limits."""
    name = cfg["session_hosts"][0]
    host = lab.host(name)
    a1, a2 = cfg["users"]["agent"]["username"], cfg["users"]["agent_b"]["username"]
    uid1, uid2 = (host.check_output(f"id -u {q(u)}") for u in (a1, a2))
    crm_ip = host.check_output("getent hosts crm.lab.example.com | awk '{print $1}'")
    start = c.get("/api/policy")["rings"]["broad"]
    c.post(f"/api/devices/{name}/ring", {"ring": "pilot"})
    try:
        c.post("/api/policy", {
            "egress_mode": "off", "limits": {"mem_mb": 3072},
            "assignments": [
                {"target": "group:CC-Agents-Test", "egress_mode": "inherit", "limits": {"cpu_pct": 150, "mem_mb": 1536}},
                {"target": f"user:{a2}", "egress_mode": "allow_only",
                 "egress_allow": [{"name": "CRM", "dest": f"{crm_ip}/32", "proto": "tcp", "ports": "80"}]},
            ]})
        wait_for(lambda: not tcp_as(host, a2, *DC_LDAP), 45, what=f"{a2} limited by the user assignment")
        assert tcp_as(host, a2, crm_ip, 80), f"{a2} keeps the destination its assignment allows"
        assert tcp_as(host, a1, *DC_LDAP), f"{a1} (group only) is not firewalled"
        wait_for(lambda: slice_value(host, uid1, "MemoryMax") == str(1536 * 2**20), 30, what="group RAM limit for agent1")
        assert slice_value(host, uid1, "CPUQuotaPerSecUSec") == "1.500000s"
        assert slice_value(host, uid2, "MemoryMax") == str(3072 * 2**20), "user assignment wins: everyone's limit"
        applied = lambda: next(d for d in c.get("/api/policy")["devices"] if d["id"] == name)["applied"]
        wait_for(lambda: applied().get("assigned_users", {}).get(f"user:{a2}") == 1, 30,
                 what="device reports the users each assignment covers")
    finally:
        c.post("/api/policy/rollback", {"version": start})
        c.post(f"/api/devices/{name}/ring", {"ring": "broad"})
    wait_for(lambda: tcp_as(host, a2, *DC_LDAP), 45, what="assignment removed after rollback")
    wait_for(lambda: slice_value(host, uid1, "MemoryMax") == str(4 * 2**30), 30, what="server default limits back")
