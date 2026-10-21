"""Checklist: home endpoint - VPN connects before login, internal apps reachable.
(Internal apps from home are also covered by the app inventory, test_04.)"""
import time

import pytest

from lib import pam_login, tcp_open, wait_for


def _wg(cfg):
    return cfg["vpn"]["interface"]


def test_vpn_enabled_at_boot_and_up(lab, cfg, home_endpoint):
    svc = lab.host(home_endpoint).service(f"wg-quick@{_wg(cfg)}")
    assert svc.is_enabled and svc.is_running


def test_vpn_starts_before_anyone_can_log_in(lab, cfg, home_endpoint):
    before = lab.host(home_endpoint).check_output(f"systemctl show -p Before --value wg-quick@{_wg(cfg)}")
    assert "systemd-user-sessions.service" in before.split()


def test_tunnel_has_a_live_handshake(lab, cfg, home_endpoint):
    h = lab.host(home_endpoint)
    tcp_open(h, cfg["vpn"]["internal_probe"])
    out = h.check_output(f"wg show {_wg(cfg)} latest-handshakes")
    assert int(out.split()[-1]) > time.time() - 300, out


def test_internal_network_via_tunnel(lab, cfg, home_endpoint):
    h = lab.host(home_endpoint)
    ip = cfg["vpn"]["internal_probe"].split(":")[0]
    assert f"dev {_wg(cfg)}" in h.check_output(f"ip route get {ip}")
    assert tcp_open(h, cfg["vpn"]["internal_probe"])


def test_ad_login_from_home(lab, cfg, home_endpoint):
    u = cfg["users"]["agent"]
    assert pam_login(lab.host(home_endpoint), u["username"], u["password"]).rc == 0


@pytest.mark.slow
def test_no_internal_access_without_the_tunnel(lab, cfg, home_endpoint):
    """With the VPN down nothing internal is reachable, not even by routing
    straight at the VPN gateway's internet address."""
    h = lab.host(home_endpoint)
    wg = _wg(cfg)
    probe = cfg["vpn"]["internal_probe"]
    h.check_output(f"systemctl stop wg-quick@{wg}")
    try:
        assert not tcp_open(h, probe, timeout=3)
        h.run(f"ip route add {cfg['vpn']['internal_subnet']} via {cfg['vpn']['gateway_internet_ip']}")
        try:
            assert not tcp_open(h, probe, timeout=3), "gateway forwards traffic that did not come through the tunnel"
        finally:
            h.run(f"ip route del {cfg['vpn']['internal_subnet']} via {cfg['vpn']['gateway_internet_ip']}")
    finally:
        h.check_output(f"systemctl start wg-quick@{wg}")
        wait_for(lambda: tcp_open(h, probe), 30, what="tunnel back up")


@pytest.mark.lab_only
def test_home_endpoint_disk_is_encrypted(lab, home_endpoint):
    assert "crypt" in lab.host(home_endpoint).check_output("lsblk -n -o TYPE").split()
