"""Checklist: session host failure - users reconnect to the other host.
Also: a disconnected user gets back to their own running session."""
import subprocess
import time

import pytest

from lib import RdpClient, desktop_pid, end_desktops, session_host_of, wait_for

pytestmark = pytest.mark.slow


def test_load_balancer_config(lab, cfg):
    if not lab.sim:
        pytest.skip("checked on the real HAProxy pair by the next tests")
    conf = lab.host("lb").file("/usr/local/etc/haproxy/haproxy.cfg").content_string
    assert "balance leastconn" in conf
    assert "stick on" in conf and "option redispatch" in conf


def test_reconnect_and_failover(lab, cfg):
    u = cfg["users"]["agent_b"]
    user = u["username"]
    endpoint = lab.host(cfg["office_endpoints"][0])
    rdp = RdpClient(endpoint, cfg["rdp_host"], user, u["password"])
    end_desktops(lab, user)

    # 1. First login lands somewhere
    rdp.connect()
    first = wait_for(lambda: session_host_of(lab, user), 60, what="first session")
    pid = desktop_pid(lab, first, user)
    rdp.disconnect()

    # 2. Reconnect: same host, same running desktop
    time.sleep(3)
    rdp.connect()
    time.sleep(15)
    assert session_host_of(lab, user) == first
    assert desktop_pid(lab, first, user) == pid, "user got a new session instead of their own"
    rdp.disconnect()

    # 3. The host fails: the user must get a desktop on another host
    subprocess.run(lab.fmt(cfg["power_off"], first), shell=True, check=True)
    try:
        time.sleep(6)        # health checks: inter 2s, fall 2
        rdp.connect()
        second = wait_for(lambda: session_host_of(lab, user), 90, what="session on surviving host")
        assert second != first
    finally:
        rdp.disconnect()
        end_desktops(lab, user)
        subprocess.run(lab.fmt(cfg["power_on"], first), shell=True, check=True)
        # 4. The failed host comes back by itself (services enabled at boot)
        wait_for(lambda: _serving(lab, first), 180, what=f"{first} back in service")


def _serving(lab, name):
    try:
        h = lab.host(name)
        return h.service("xrdp").is_running and h.service("sssd").is_running
    except Exception:
        return False
