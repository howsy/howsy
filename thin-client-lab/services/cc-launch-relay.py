#!/usr/bin/env python3
"""Session-host relay for "open on my laptop" (runs as root).
Users write an app id to /run/cc-launch.sock; the kernel tells us which user
it is (SO_PEERCRED), so a user can only ever open apps on their own laptop."""
import json
import os
import pwd
import socket
import struct
import threading
import urllib.request

conf = json.load(open("/etc/cc-broker/client.json"))
SOCK = "/run/cc-launch.sock"


def handle(conn):
    with conn:
        pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        app = conn.recv(64).decode(errors="replace").strip()
        user = pwd.getpwuid(uid).pw_name
        req = urllib.request.Request(
            conf["broker_url"] + "/launch",
            data=json.dumps({"user": user, "app": app}).encode(),
            headers={"Content-Type": "application/json", "X-CC-Host-Token": conf["host_token"]},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                conn.sendall(b"ok\n" if r.status == 202 else b"error\n")
        except Exception as e:
            conn.sendall(f"error {e}\n".encode())


def main():
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK)
    os.chmod(SOCK, 0o666)
    srv.listen(64)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
