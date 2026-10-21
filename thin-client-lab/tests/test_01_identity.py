"""Checklist: AD login works on endpoint, session host and portal;
a user outside the group is refused.  (Portal side: test_08_portal.py)"""
from lib import pam_login

# Session hosts are checked through the PAM service xrdp itself uses.
def _service(cfg, name):
    return "xrdp-sesman" if name in cfg["session_hosts"] else "login"


def test_host_is_joined_to_the_domain(lab, login_host):
    h = lab.host(login_host)
    assert h.run(f"adcli testjoin --domain {lab.cfg['domain']}").rc == 0
    assert h.service("sssd").is_running


def test_agent_can_log_in(lab, cfg, login_host):
    u = cfg["users"]["agent"]
    r = pam_login(lab.host(login_host), u["username"], u["password"], _service(cfg, login_host))
    assert r.rc == 0, r.stdout + r.stderr


def test_wrong_password_is_refused(lab, cfg, login_host):
    u = cfg["users"]["agent"]
    r = pam_login(lab.host(login_host), u["username"], "not-the-password", _service(cfg, login_host))
    assert r.rc != 0


def test_user_outside_the_group_is_refused(lab, cfg, login_host):
    """Valid AD credentials, but not in the agents group: authentication passes,
    account check must deny."""
    u = cfg["users"]["outsider"]
    r = pam_login(lab.host(login_host), u["username"], u["password"], _service(cfg, login_host))
    assert r.rc != 0
    assert "Permission denied" in r.stdout + r.stderr


def test_username_without_domain_suffix(lab, cfg, login_host):
    """Agents type their Windows username without @domain."""
    u = cfg["users"]["agent"]["username"]
    out = lab.host(login_host).check_output(f"id -un {u}")
    assert out == u
