"""Checklist: USB storage blocked and no admin rights on endpoints.
Plus Chrome lock-down, screen lock and the farm shortcut from the design."""
import json

import pytest

pytestmark = pytest.mark.endpoint


def test_usb_storage_driver_blocked(lab, endpoint):
    h = lab.host(endpoint)
    for mod in ("usb-storage", "uas"):
        out = h.run(f"modprobe -n -v {mod}").stdout
        assert "install /bin/false" in out, f"{mod}: {out}"


@pytest.mark.lab_only
def test_usb_storage_not_loaded(lab, endpoint):
    assert "usb_storage" not in lab.host(endpoint).check_output("lsmod")


def test_removable_media_mount_denied_by_polkit(lab, endpoint):
    rule = lab.host(endpoint).file("/etc/polkit-1/rules.d/50-cc-no-removable-mount.rules")
    assert rule.exists and "udisks2.filesystem-mount" in rule.content_string


def test_agent_has_no_admin_rights(lab, cfg, endpoint):
    h = lab.host(endpoint)
    user = cfg["users"]["agent"]["username"]
    out = h.run(f"sudo -l -U {user}").stdout
    assert "not allowed to run sudo" in out, out
    for grp in ("sudo", "admin", "adm", "lpadmin"):
        members = h.run(f"getent group {grp}").stdout
        assert user not in members.split(":")[-1].split(","), f"{user} in {grp}"


def test_screen_locks_after_idle_and_agent_cannot_change_it(lab, cfg, endpoint):
    h = lab.host(endpoint)
    user = cfg["users"]["agent"]["username"]
    def gs(args):
        return h.check_output(f"su - {user} -c 'dbus-run-session -- gsettings {args}'")
    assert gs("get org.gnome.desktop.screensaver lock-enabled") == "true"
    idle = int(gs("get org.gnome.desktop.session idle-delay").split()[-1])
    assert 0 < idle <= 900
    for key in ("org.gnome.desktop.session idle-delay", "org.gnome.desktop.screensaver lock-enabled"):
        assert gs(f"writable {key}") == "false", f"{key} not locked"


def test_chrome_locked_by_policy(lab, endpoint):
    pol = json.loads(lab.host(endpoint).file("/etc/opt/chrome/policies/managed/cc-policy.json").content_string)
    assert pol["DeveloperToolsAvailability"] == 2
    assert pol["IncognitoModeAvailability"] == 1
    assert pol["DownloadRestrictions"] == 3, "downloads would put customer data on the endpoint"
    assert pol["ExtensionInstallBlocklist"] == ["*"]
    assert pol["BrowserGuestModeEnabled"] is False
    assert "file://*" in pol["URLBlocklist"]


def test_shortcut_to_session_host_farm(lab, cfg, endpoint):
    h = lab.host(endpoint)
    assert h.file("/usr/share/applications/cc-desktop.desktop").exists
    profile = h.file("/etc/cc/cc-desktop.remmina").content_string
    assert f"server={cfg['rdp_host']}" in profile
    assert "disableclipboard=1" in profile
    assert h.package("remmina-plugin-rdp").is_installed
