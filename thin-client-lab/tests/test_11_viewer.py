"""Viewer flow: the agent's own (Windows) laptop only shows the server desktop.

  laptop login (AD) -> Kerberos -> broker one-time ticket -> RDP opens, no 2nd password
  session desktop   -> icon per web system, browser with every system as tabs
  Genesys icon      -> broker -> the agent's own laptop starts / focuses Genesys

The laptop is played by a Linux endpoint running services/cc-laptop-agent.py,
which speaks the same protocol as windows/CC-Shell.ps1."""
import json
import time

import pytest

from lib import end_desktops, q, session_host_of, wait_for

pytestmark = pytest.mark.skipif("viewer" not in __import__("conftest").CFG, reason="no viewer flow in targets")


@pytest.fixture(scope="module")
def v(cfg):
    return cfg["viewer"]


@pytest.fixture(scope="module")
def laptop(lab, v):
    return lab.host(v["laptop"])


def as_user(host, user, password, cmd):
    """Run cmd on the laptop as an AD user who has just logged in (has a Kerberos ticket)."""
    return host.run(f"su - {q(user)} -c {q(f'printf %s {q(password)} | kinit {q(user)} >/dev/null && {cmd}')}")


def health(lab, v):
    return json.loads(lab.host(v["broker_host"]).check_output(
        "python3 -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8088/healthz').read().decode())\""))


def test_broker_running_with_http_principal(lab, v):
    h = lab.host(v["broker_host"])
    assert h.service("cc-broker").is_running and h.service("cc-broker").is_enabled
    assert "HTTP/" in h.check_output("klist -k /etc/cc-broker/http.keytab")


def test_ticket_requires_kerberos(laptop, v):
    code = laptop.check_output(
        "python3 -c \"import urllib.request,urllib.error\n"
        f"try: urllib.request.urlopen(urllib.request.Request('{v['broker_url']}/sso/ticket', method='POST'))\n"
        "except urllib.error.HTTPError as e: print(e.code, e.headers.get('WWW-Authenticate'))\"")
    assert code == "401 Negotiate"


def test_no_ticket_for_user_outside_the_group(lab, cfg, laptop):
    """The laptop already refuses to log outsiders in; the broker must refuse them
    too, even with a valid Kerberos ticket obtained some other way."""
    u = cfg["users"]["outsider"]
    cc = "KRB5CCNAME=FILE:/tmp/krb5cc_outsider_test"
    r = laptop.run(f"printf %s {q(u['password'])} | {cc} kinit {q(u['username'])} >/dev/null && {cc} cc-sso-ticket")
    laptop.run("rm -f /tmp/krb5cc_outsider_test")
    assert r.rc != 0 and "403" in r.stdout, r.stdout + r.stderr


def _ticket(laptop, u):
    r = as_user(laptop, u["username"], u["password"], "cc-sso-ticket")
    assert r.rc == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def _pam(host, user, password):
    return host.run(f"printf '%s\\n' {q(password)} | pamtester xrdp-sesman {q(user)} authenticate").rc == 0


def test_ticket_is_single_use_and_bound_to_its_user(lab, cfg, laptop):
    sh = lab.host(cfg["session_hosts"][0])
    agent, other = cfg["users"]["agent"], cfg["users"]["agent_b"]
    t = _ticket(laptop, agent)
    assert t["username"] == agent["username"] and t["password"].startswith("ccsso-")
    assert not _pam(sh, other["username"], t["password"]), "ticket accepted for another user"
    t = _ticket(laptop, agent)
    assert _pam(sh, agent["username"], t["password"]), "valid ticket refused"
    assert not _pam(sh, agent["username"], t["password"]), "ticket accepted twice"


def test_ad_passwords_never_sent_to_broker(lab, cfg, v):
    """Normal password logins are handled by SSSD; only 'ccsso-' tickets reach the broker."""
    u = cfg["users"]["agent"]
    before = health(lab, v)
    assert _pam(lab.host(cfg["session_hosts"][0]), u["username"], u["password"])
    after = health(lab, v)
    assert (after["verify_ok"], after["verify_fail"]) == (before["verify_ok"], before["verify_fail"])


@pytest.fixture
def viewer_session(lab, cfg, laptop):
    """Agent logs in to the laptop; the laptop opens the server desktop by itself."""
    u = cfg["users"]["agent"]
    user = u["username"]
    end_desktops(lab, user)
    laptop.run(f"pkill -u {user}; rm -f /tmp/genesys-desktop-{user}.log /tmp/laptop-{user}.log")
    as_user(laptop, user, u["password"], f"nohup setsid cc-laptop-session > /tmp/laptop-{user}.log 2>&1 &")
    yield user
    laptop.run(f"pkill -u {user}")
    end_desktops(lab, user)


def _genesys_log(laptop, user):
    return laptop.run(f"cat /tmp/genesys-desktop-{user}.log").stdout


def test_laptop_login_opens_desktop_and_genesys_without_second_password(lab, laptop, viewer_session):
    user = viewer_session
    where = wait_for(lambda: session_host_of(lab, user), 60, what="server desktop opened by the laptop")
    assert "/p:ccsso-" in laptop.check_output(f"ps -o args= -u {user} | grep xfreerdp3 | grep -v grep || true"), \
        "RDP was not opened with a one-time ticket"
    wait_for(lambda: "started" in _genesys_log(laptop, user), 45, what="Genesys started on the laptop")
    assert where in lab.cfg["session_hosts"]


def test_genesys_icon_in_session_opens_on_own_laptop_only(lab, cfg, laptop, viewer_session):
    user = viewer_session
    where = wait_for(lambda: session_host_of(lab, user), 60, what="server desktop")
    wait_for(lambda: "started" in _genesys_log(laptop, user), 45, what="Genesys started")
    sh = lab.host(where)
    focus_before = _genesys_log(laptop, user).count("focus")

    # Another agent's Genesys icon must not reach this laptop.
    other = cfg["users"]["agent_b"]["username"]
    sh.check_output(f"su - {other} -c 'cc-local-launch genesys'")
    time.sleep(5)
    assert _genesys_log(laptop, user).count("focus") == focus_before

    # The agent's own desktop icon (run exactly as the desktop would) brings Genesys forward.
    exec_line = sh.check_output(f"grep '^Exec=' /home/{user}/Desktop/cc-local-genesys.desktop").split("=", 1)[1]
    sh.check_output(f"su - {user} -c {q(exec_line)}")
    wait_for(lambda: _genesys_log(laptop, user).count("focus") > focus_before, 30, what="Genesys brought to front")


def test_only_allowed_apps_can_be_opened_on_laptops(lab, cfg):
    sh = lab.host(cfg["session_hosts"][0])
    r = sh.run(f"su - {cfg['users']['agent']['username']} -c 'cc-local-launch powershell'")
    assert r.rc != 0 and "400" in r.stdout


def test_closing_the_desktop_ends_the_laptop_session(lab, laptop, viewer_session):
    user = viewer_session
    wait_for(lambda: session_host_of(lab, user), 60, what="server desktop")
    laptop.run(f"pkill -u {user} -x xfreerdp3")      # the agent closes the remote desktop window
    wait_for(lambda: "sign out" in laptop.run(f"cat /tmp/laptop-{user}.log").stdout, 30, what="laptop sign-out")


# --- the agent desktop on the session hosts ----------------------------------

def test_desktop_has_an_icon_per_system_and_genesys(lab, cfg, session_host):
    from test_04_apps import APPS
    h = lab.host(session_host)
    names = h.check_output("grep -h '^Name=' /usr/share/cc-desktop/*.desktop | cut -d= -f2-").splitlines()
    assert sorted(names) == sorted([a["name"] for a in APPS] + ["Genesys Cloud"])


def test_browser_opens_every_system_as_a_tab(lab, cfg, session_host):
    from test_04_apps import APPS
    pol = json.loads(lab.host(session_host).file("/etc/opt/chrome/policies/managed/cc-desktop.json").content_string)
    urls = [a["url"] for a in APPS]
    assert pol["RestoreOnStartup"] == 4 and pol["RestoreOnStartupURLs"] == urls
    assert [b["url"] for b in pol["ManagedBookmarks"][1:]] == urls


def test_agents_cannot_shut_down_or_open_a_terminal(lab, cfg, session_host):
    h = lab.host(session_host)
    assert "Shutdown=%sudo" in h.file("/etc/xdg/xfce4/kiosk/kioskrc").content_string
    user = cfg["users"]["agent"]["username"]
    r = h.run(f"su - {user} -c '/usr/bin/xfce4-terminal --version'")
    assert r.rc != 0 and "Permission denied" in r.stderr + r.stdout


def test_each_agent_gets_a_capped_share_of_the_server(lab, cfg, session_host):
    h = lab.host(session_host)
    user = cfg["users"]["agent"]["username"]
    uid = h.check_output(f"id -u {user}")
    h.run(f"su - {user} -c true")
    props = h.check_output(f"systemctl show -p MemoryMax -p CPUQuotaPerSecUSec user-{uid}.slice")
    assert "MemoryMax=infinity" not in props and "CPUQuotaPerSecUSec=infinity" not in props, props
