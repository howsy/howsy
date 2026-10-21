"""Checklist: password change and expired-password prompt work from the endpoint."""
import secrets
import time

import pexpect
import pytest

from lib import pam_login, q, wait_for


@pytest.fixture
def expired_user(lab, cfg):
    """A user whose password must be changed at next logon."""
    pt = cfg["password_test"]
    if pt["mode"] == "prepared":
        yield pt["username"], pt["password"]
        return
    dc = lab.host(pt["dc_host"])
    name = "pwtest" + secrets.token_hex(3)
    password = "Old-Pass1!" + secrets.token_hex(2)
    dc.check_output(
        f"samba-tool user create {name} {q(password)} --userou=OU=CallCenterTest --must-change-at-next-login"
    )
    dc.check_output(f"samba-tool group addmembers {q(pt['group'])} {name}")
    yield name, password
    dc.run(f"samba-tool user delete {name}")


def test_expired_password_prompts_for_change(lab, cfg, office_endpoint, expired_user):
    name, old = expired_user
    new = "New-Pass1!" + secrets.token_hex(2)
    h = lab.host(office_endpoint)
    wait_for(lambda: h.run(f"id {name}").rc == 0, 90, what=f"{name} visible through SSSD")

    # Log in as an ordinary user would (su from an unprivileged account runs the
    # full PAM auth -> account -> password-change sequence, like the login screen).
    tty = lab.fmt(cfg["tty_command"], office_endpoint)
    child = pexpect.spawn(f"{tty} runuser -u nobody -- su - {name}", encoding="utf-8", timeout=30)
    try:
        child.expect("Password:")
        child.sendline(old)
        child.expect(r"(?i)password expired|change your password|must change")
        i = child.expect([r"(?i)current password:", r"(?i)new password:"])
        if i == 0:
            child.sendline(old)
            child.expect(r"(?i)new password:")
        child.sendline(new)
        child.expect(r"(?i)(retype|reenter|re-enter).*password:")
        child.sendline(new)
        child.expect(r"[$#] ?$|@", timeout=30)
        child.sendline("id -un; exit")
        child.expect(name)
    finally:
        log = child.before
        child.close()

    time.sleep(2)
    assert pam_login(h, name, new).rc == 0, f"new password refused; last output: {log!r}"
    assert pam_login(h, name, old).rc != 0, "old password still accepted"
