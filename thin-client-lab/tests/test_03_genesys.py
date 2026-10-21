"""Checklist: Genesys Cloud web app in app mode (automated readiness checks).
Calls, transfer, wrap-up and audio are manual: docs/MANUAL_TESTS.md (G-1..G-6)."""
import json

import pytest

from lib import http_get, q

pytestmark = pytest.mark.endpoint


def test_genesys_opens_in_app_mode_at_login(lab, cfg, endpoint):
    h = lab.host(endpoint)
    launcher = h.file("/usr/local/bin/cc-genesys")
    assert launcher.is_file and launcher.mode & 0o111
    assert f"--app={cfg['genesys_org_url']}" in launcher.content_string
    unit = h.file("/etc/systemd/user/cc-genesys.service").content_string
    assert "Restart=always" in unit, "Genesys window must come back if closed"
    assert h.file("/etc/systemd/user/graphical-session.target.wants/cc-genesys.service").exists


def test_chrome_installed(lab, cfg, endpoint):
    if str(cfg.get("chrome_expected", "true")).lower() != "true":
        pytest.skip("Chrome download disabled for this run (CHROME_INSTALL=false)")
    assert lab.host(endpoint).package("google-chrome-stable").is_installed


def test_microphone_allowed_for_genesys_without_prompt(lab, endpoint):
    pol = json.loads(lab.host(endpoint).file("/etc/opt/chrome/policies/managed/cc-policy.json").content_string)
    assert any("mypurecloud" in p or "pure.cloud" in p for p in pol["AudioCaptureAllowedUrls"])
    assert pol["NotificationsAllowedForUrls"], "incoming-call notifications must be allowed"


def test_genesys_cloud_reachable(lab, cfg, endpoint):
    status, body = http_get(lab.host(endpoint), cfg["genesys_probe"][endpoint])
    assert status is not None and status < 500, body


def test_home_voice_bypasses_the_tunnel(lab, cfg, home_endpoint):
    """Split tunnel: Genesys Cloud media must leave via the home internet link."""
    h = lab.host(home_endpoint)
    ip = cfg.get("genesys_direct_ip") or h.check_output(
        f"getent ahostsv4 {q(cfg['genesys_org_url'].split('/')[2])} | head -1 | cut -d' ' -f1")
    route = h.check_output(f"ip route get {ip}")
    assert f"dev {cfg['vpn']['interface']}" not in route, route
