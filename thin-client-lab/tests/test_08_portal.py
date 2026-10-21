"""Checklist: portal - AD login + MFA from an outside network, clipboard and
file transfer blocked. Plus the portal hardening controls from the design."""
import os
import time
import urllib.parse

import pyotp
import pytest
import requests
import urllib3
import websocket

from lib import q, session_host_of, end_desktops, wait_for

urllib3.disable_warnings()


@pytest.fixture(scope="module")
def portal(cfg):
    return cfg["portal"]


def _post_login(portal, **fields):
    return requests.post(f"{portal['url']}/guacamole/api/tokens", data=fields,
                         verify=portal["verify_tls"], timeout=15)


@pytest.fixture(scope="module")
def agent_token(lab, cfg, portal):
    """Logs in as the agent with AD password + TOTP (enrolling on first use)."""
    u = cfg["users"]["agent"]
    if lab.sim:  # start from a clean enrolment so the test knows the secret
        lab.host(portal["psql_host"]).run(portal["psql"] + " " + q(
            "DELETE FROM guacamole_user_attribute WHERE attribute_name LIKE 'guac-totp%'"))
    r = _post_login(portal, username=u["username"], password=u["password"])
    body = r.json()
    assert r.status_code == 403 and body["type"] == "INSUFFICIENT_CREDENTIALS", body
    field = next(f for f in body["expected"] if f["name"] == "guac-totp")
    # Real lab: the test user is already enrolled; pass the key from their authenticator.
    secret = field.get("secret") or os.environ.get("TOTP_SECRET")
    if not secret:
        pytest.skip("agent already enrolled in TOTP; set TOTP_SECRET or reset enrolment")
    totp = pyotp.TOTP(secret)
    r = _post_login(portal, username=u["username"], password=u["password"], **{"guac-totp": totp.now()})
    assert r.status_code == 200, r.text
    return {"token": r.json()["authToken"], "totp": totp}


def test_http_redirects_to_https(lab, cfg, portal):
    out = lab.host(cfg["outside_host"]).check_output(
        f"curl -s -o /dev/null -w '%{{http_code}} %{{redirect_url}}' http://{portal['public_ip']}/")
    code, url = out.split(" ", 1)
    assert code == "301" and url.startswith("https://")


def test_old_tls_refused(lab, cfg, portal):
    r = lab.host(cfg["outside_host"]).run(
        f"curl -sk --tlsv1.1 --tls-max 1.1 -o /dev/null https://{portal['public_ip']}/")
    assert r.rc != 0


def test_security_headers(portal):
    r = requests.get(f"{portal['url']}/guacamole/", verify=portal["verify_tls"], timeout=15)
    assert "max-age" in r.headers.get("Strict-Transport-Security", "")
    assert "nginx/" not in r.headers.get("Server", ""), "server version leaked"


def test_only_the_proxy_is_exposed(lab, cfg, portal):
    outside = lab.host(cfg["outside_host"])
    out = outside.check_output(
        f"nmap -Pn -n --open -p 21,22,23,80,389,443,445,3389,5432,8080,4822 {portal['public_ip']} -oG - | grep Ports: || true")
    open_ports = sorted(int(p.split("/")[0]) for p in out.split("Ports:")[-1].split(",") if "/open/" in p)
    assert open_ports == sorted(portal["open_ports"]), out
    for target in cfg["internal_targets"]:
        ip, port = target.split(":")
        r = outside.run(f"nc -z -w 3 {ip} {port}")
        assert r.rc != 0, f"{target} reachable from the internet"


def test_agent_login_requires_totp(agent_token):
    assert agent_token["token"]


def test_wrong_totp_code_refused(cfg, portal, agent_token):
    u = cfg["users"]["agent"]
    bad = "000000" if agent_token["totp"].now() != "000000" else "111111"
    r = _post_login(portal, username=u["username"], password=u["password"], **{"guac-totp": bad})
    assert r.status_code in (400, 403) and "authToken" not in r.text, r.text


def test_outsider_refused(cfg, portal):
    u = cfg["users"]["outsider"]
    r = _post_login(portal, username=u["username"], password=u["password"])
    assert r.status_code == 403 and r.json()["type"] == "INVALID_CREDENTIALS"


def test_default_guacadmin_disabled(portal):
    r = _post_login(portal, username="guacadmin", password="guacadmin")
    assert r.status_code == 403 and r.json()["type"] == "INVALID_CREDENTIALS"


def _desktop_group(portal, token):
    r = requests.get(
        f"{portal['url']}/guacamole/api/session/data/{portal['data_source']}/connectionGroups/ROOT/tree",
        params={"token": token}, verify=portal["verify_tls"], timeout=15)
    r.raise_for_status()
    groups = r.json().get("childConnectionGroups", [])
    return next(g for g in groups if g["name"] == portal["group_name"])


def test_agent_sees_balanced_desktop_group(portal, agent_token, cfg):
    g = _desktop_group(portal, agent_token["token"])
    assert g["type"] == "BALANCING"
    assert len(g.get("childConnections", [])) == len(cfg["session_hosts"])


def _params(lab, portal):
    sql = ("SELECT c.connection_name, p.parameter_name, p.parameter_value "
           "FROM guacamole_connection c JOIN guacamole_connection_parameter p USING (connection_id) "
           "JOIN guacamole_connection_group g ON g.connection_group_id = c.parent_id "
           f"WHERE g.connection_group_name = '{portal['group_name']}'")
    rows = lab.host(portal["psql_host"]).check_output(portal["psql"] + " " + q(sql)).splitlines()
    params = {}
    for row in rows:
        conn, name, value = row.split("|", 2)
        params.setdefault(conn, {})[name] = value
    return params


def test_clipboard_and_file_transfer_blocked(lab, portal):
    params = _params(lab, portal)
    assert params, "no portal connections found"
    for conn, p in params.items():
        assert p.get("disable-copy") == "true" and p.get("disable-paste") == "true", conn
        assert p.get("enable-drive", "false") != "true", conn
        assert p.get("enable-sftp", "false") != "true", conn
        assert p.get("enable-printing", "false") != "true", conn


def test_sessions_are_recorded(lab, portal):
    for conn, p in _params(lab, portal).items():
        assert p.get("recording-path"), f"{conn} not recorded"


def test_desktop_opens_in_browser_and_is_recorded(lab, cfg, portal, agent_token):
    """Opens the desktop over the Guacamole websocket like a browser would."""
    user = cfg["users"]["agent"]["username"]
    end_desktops(lab, user)
    g = _desktop_group(portal, agent_token["token"])
    qs = urllib.parse.urlencode({
        "token": agent_token["token"], "GUAC_DATA_SOURCE": portal["data_source"],
        "GUAC_ID": g["identifier"], "GUAC_TYPE": "g", "GUAC_WIDTH": 1280, "GUAC_HEIGHT": 800,
        "GUAC_DPI": 96, "GUAC_IMAGE": "image/png", "GUAC_TIMEZONE": "UTC"})
    ws_url = portal["url"].replace("https://", "wss://") + "/guacamole/websocket-tunnel?" + qs
    sslopt = {} if portal["verify_tls"] else {"cert_reqs": 0, "check_hostname": False}
    ws = websocket.create_connection(ws_url, subprotocols=["guacamole"], sslopt=sslopt, timeout=30)
    received = ""
    try:
        deadline = time.time() + 45
        while time.time() < deadline and "4.sync" not in received:
            received += ws.recv()
            assert "5.error" not in received, received[-300:]
        assert "4.sync" in received, "no desktop frames received"
        wait_for(lambda: session_host_of(lab, user), 45, what="desktop on a session host")
    finally:
        ws.close()
        end_desktops(lab, user)
    rec = lab.host(portal["recordings_host"]).check_output(f"ls -1 {portal['recordings_path']}")
    assert any(f.startswith(user) for f in rec.split()), rec


@pytest.mark.slow
def test_brute_force_is_rate_limited_then_banned(lab, cfg, portal):
    outside = lab.host(cfg["outside_host"])
    ip = portal.get("outside_ip") or outside.check_output("hostname -I").split()[0]
    url = f"https://{portal['public_ip']}/guacamole/api/tokens"
    codes = outside.check_output(
        "for i in $(seq 40); do curl -sk -o /dev/null -w '%{http_code} ' "
        f"-d username=attacker -d password=guess$i {url}; done")
    assert "429" in codes.split(), f"no rate limiting: {codes}"
    proxy = lab.host("proxy") if "proxy" in cfg["hosts"] else None
    try:
        if proxy:
            wait_for(lambda: ip in proxy.check_output(
                f"fail2ban-client status {portal['fail2ban_jail']}"), 30, what="fail2ban ban")
        r = outside.run(f"curl -sk -m 5 -o /dev/null -w '%{{http_code}}' https://{portal['public_ip']}/guacamole/")
        assert r.rc != 0, "banned client can still reach the portal"
    finally:
        if proxy:
            proxy.run(f"fail2ban-client set {portal['fail2ban_jail']} unbanip {ip}")
