"""Helpers used by several test modules."""
import shlex
import time


def q(s):
    return shlex.quote(str(s))


def wait_for(predicate, timeout=60, interval=2, what="condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {what} (last: {last!r})")


def pam_login(host, user, password, service="login"):
    """Authenticate + account check through the host's PAM stack (SSSD -> AD).
    Returns the command result; rc 0 means the user could log in."""
    return host.run(
        f"printf '%s\\n' {q(password)} | timeout 30 pamtester {q(service)} {q(user)} authenticate acct_mgmt"
    )


def tcp_open(host, target, timeout=5):
    """True if host can open a TCP connection to "ip:port"."""
    ip, port = target.rsplit(":", 1)
    script = (
        "import socket,sys\n"
        "s=socket.socket(); s.settimeout(%d)\n"
        "try:\n    s.connect((%r,%s)); print('open')\n"
        "except Exception as e:\n    print('closed', e); sys.exit(1)\n"
    ) % (timeout, ip, port)
    return host.run(f"python3 -c {q(script)}").rc == 0


def http_get(host, url, timeout=10):
    """Fetch a URL from the host itself. Returns (status, body) or (None, error)."""
    script = (
        "import urllib.request,ssl,sys\n"
        "ctx=ssl.create_default_context()\n"
        "try:\n"
        "    r=urllib.request.urlopen(%r, timeout=%d, context=ctx)\n"
        "    print(r.status); print(r.read(4000).decode('utf-8','replace'))\n"
        "except urllib.error.HTTPError as e:\n    print(e.code)\n"
        "except Exception as e:\n    print('ERR'); print(e); sys.exit(1)\n"
    ) % (url, timeout)
    out = host.run(f"python3 -c {q(script)}").stdout
    first, _, rest = out.partition("\n")
    return (int(first) if first.strip().isdigit() else None), rest


def session_host_of(lab, user):
    """Name of the session host where user has a running desktop, or None."""
    for name in lab.cfg["session_hosts"]:
        try:
            r = lab.host(name).run(f"pgrep -u {q(user)} -f xfce4-session")
        except Exception:      # host powered off
            continue
        if r.rc == 0:
            return name
    return None


def desktop_pid(lab, host_name, user):
    r = lab.host(host_name).run(f"pgrep -o -u {q(user)} -f xfce4-session")
    return r.stdout.strip() or None


def end_desktops(lab, user):
    for name in lab.cfg["session_hosts"]:
        try:
            lab.host(name).run(f"pkill -KILL -u {q(user)}")
        except Exception:
            pass


class RdpClient:
    """FreeRDP run on an endpoint (headless via Xvfb), like an agent clicking
    the Call Center Desktop shortcut."""

    def __init__(self, endpoint_host, server, user, password):
        self.h, self.server, self.user, self.password = endpoint_host, server, user, password
        self.tag = f"ccrdp-{user}"

    def connect(self):
        self.disconnect()
        cmd = (
            f"nohup setsid xvfb-run -a xfreerdp3 /v:{q(self.server)} /u:{q(self.user)} "
            f"/p:{q(self.password)} /cert:ignore /size:1280x800 /log-level:ERROR "
            f"/t:{q(self.tag)} >/tmp/{self.tag}.log 2>&1 &"
        )
        self.h.run(cmd)

    def disconnect(self):
        self.h.run(f"pkill -f {q('[/]t:' + self.tag)}")   # [/] keeps pkill from matching this shell
        time.sleep(1)

    def log(self):
        return self.h.run(f"cat /tmp/{self.tag}.log").stdout
