"""Session-host farm: RDP desktops through the load balancer, session lockdown,
and checklist: same files and settings on either session host."""
import secrets

import pytest

from lib import RdpClient, end_desktops, q, session_host_of, wait_for


def test_xrdp_running_and_enabled(lab, session_host):
    h = lab.host(session_host)
    for svc in ("xrdp", "xrdp-sesman"):
        assert h.service(svc).is_running and h.service(svc).is_enabled
    assert h.socket("tcp://0.0.0.0:3389").is_listening or h.socket("tcp://:::3389").is_listening


def test_session_lockdown_channels(lab, session_host):
    """Clipboard, drive and printer redirection are off, and the clipboard is
    refused in both directions even if the channel were switched back on."""
    ini = lab.host(session_host).file("/etc/xrdp/xrdp.ini").content_string
    for line in ("cliprdr=false", "rdpdr=false"):
        assert f"\n{line}" in ini, line
    sesman = lab.host(session_host).file("/etc/xrdp/sesman.ini").content_string
    for line in ("RestrictOutboundClipboard=all", "RestrictInboundClipboard=all"):
        assert f"\n{line}" in sesman, line


def test_no_root_rdp_login(lab, session_host):
    assert "AllowRootLogin=false" in lab.host(session_host).file("/etc/xrdp/sesman.ini").content_string


@pytest.fixture
def office_pc(lab, cfg):
    return lab.host(cfg["office_endpoints"][0])


def test_agent_gets_desktop_through_load_balancer(lab, cfg, office_pc):
    u = cfg["users"]["agent"]
    end_desktops(lab, u["username"])
    rdp = RdpClient(office_pc, cfg["rdp_host"], u["username"], u["password"])
    rdp.connect()
    try:
        where = wait_for(lambda: session_host_of(lab, u["username"]), 60, what="XFCE session")
        assert where in cfg["session_hosts"]
    finally:
        print(rdp.log())
        rdp.disconnect()
        end_desktops(lab, u["username"])


def test_outsider_gets_no_desktop(lab, cfg, office_pc):
    u = cfg["users"]["outsider"]
    rdp = RdpClient(office_pc, cfg["rdp_host"], u["username"], u["password"])
    rdp.connect()
    try:
        with pytest.raises(AssertionError):
            wait_for(lambda: session_host_of(lab, u["username"]), 25, what="no session")
    finally:
        rdp.disconnect()


def test_same_files_on_every_session_host(lab, cfg):
    user = cfg["users"]["agent"]["username"]
    first, *others = cfg["session_hosts"]
    token = secrets.token_hex(8)
    lab.host(first).check_output(
        f"su - {user} -c {q(f'mkdir -p ~/.config && echo {token} > ~/.config/cc-roaming-probe')}")
    try:
        for name in others:
            out = lab.host(name).check_output(f"su - {user} -c 'cat ~/.config/cc-roaming-probe'")
            assert out == token, f"{name} does not see the file written on {first}"
    finally:
        lab.host(first).run(f"su - {user} -c 'rm -f ~/.config/cc-roaming-probe'")


@pytest.mark.lab_only
def test_home_folders_come_from_the_file_server(lab, cfg, session_host):
    user = cfg["users"]["agent"]["username"]
    h = lab.host(session_host)
    h.run(f"su - {user} -c true")
    fstype = h.check_output(f"findmnt -n -o FSTYPE -T /home/{user}")
    assert fstype in ("nfs4", "nfs", "cifs", "autofs"), fstype

