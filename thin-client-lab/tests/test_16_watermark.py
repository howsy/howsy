"""Watermark on catalog systems marked sensitive: the signed extension, how the
console serves it, the Chrome policy on the session hosts, and the helper that
tells the extension who is signed in. (Chrome itself is not installed in the
simulation; the extension's behaviour in Chromium is checked by hand, see
docs/DASHBOARD.md.)"""
import hashlib
import json
import struct
from pathlib import Path

import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from conftest import CFG
from lib import q, wait_for
from test_12_console import signed_in

pytestmark = pytest.mark.skipif("console" not in CFG, reason="no console in targets")
BUILD = Path(__file__).resolve().parent.parent / ".lab" / "watermark"
POLICY = "/etc/opt/chrome/policies/managed/cc-desktop.json"


def fields(buf):
    """Minimal protobuf reader: {field number: [bytes values]} for length-delimited fields."""
    out, i = {}, 0
    def varint():
        nonlocal i
        n = shift = 0
        while True:
            b = buf[i]; i += 1
            n |= (b & 0x7F) << shift; shift += 7
            if not b & 0x80:
                return n
    while i < len(buf):
        key = varint()
        length = varint()
        out.setdefault(key >> 3, []).append(buf[i:i + length]); i += length
    return out


@pytest.fixture(scope="module")
def ext_id():
    return (BUILD / "extension-id").read_text().strip()


def test_extension_is_signed_and_its_id_matches_the_key(ext_id):
    crx = (BUILD / "cc-watermark.crx").read_bytes()
    assert crx[:4] == b"Cr24" and struct.unpack("<I", crx[4:8])[0] == 3
    hlen = struct.unpack("<I", crx[8:12])[0]
    header, archive = fields(crx[12:12 + hlen]), crx[12 + hlen:]
    proof = fields(header[2][0])
    pub, sig = proof[1][0], proof[2][0]
    signed_data = header[10000][0]
    assert "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(pub).hexdigest()[:32]) == ext_id
    assert fields(signed_data)[1][0] == hashlib.sha256(pub).digest()[:16]
    msg = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed_data)) + signed_data + archive
    serialization.load_der_public_key(pub).verify(sig, msg, padding.PKCS1v15(), hashes.SHA256())   # raises if forged


def test_console_serves_the_extension_and_nothing_else(cfg, ext_id):
    url = cfg["console"]["url"]
    xml = requests.get(f"{url}/ext/updates.xml", timeout=10)
    assert xml.status_code == 200 and f"appid='{ext_id}'" in xml.text and "/ext/cc-watermark.crx" in xml.text
    crx = requests.get(f"{url}/ext/cc-watermark.crx", timeout=10)
    assert crx.status_code == 200 and crx.content == (BUILD / "cc-watermark.crx").read_bytes()
    for bad in ("/ext/../console.json", "/ext/%2e%2e/console.json", "/ext/extension-id", "/ext/"):
        assert requests.get(url + bad, timeout=10).status_code in (401, 404), bad


def test_helper_answers_with_the_desktop_user(lab, cfg, ext_id):
    host = lab.host(cfg["session_hosts"][0])
    reg = json.loads(host.file("/etc/opt/chrome/native-messaging-hosts/com.cc.watermark.json").content_string)
    assert reg["allowed_origins"] == [f"chrome-extension://{ext_id}/"], "only our extension may use the helper"
    user = cfg["users"]["agent"]["username"]
    out = host.check_output(
        f"python3 -c \"import struct,sys; m=b'{{}}'; sys.stdout.buffer.write(struct.pack('<I',len(m))+m)\" | "
        f"runuser -u {q(user)} -- {reg['path']} | tail -c +5")
    who = json.loads(out)
    assert who["user"] == user and who["host"] == cfg["session_hosts"][0]


def test_sensitive_systems_get_the_watermark_policy(lab, cfg):
    con = signed_in(cfg, "admin")
    host = lab.host(cfg["session_hosts"][0])
    pol = lambda: json.loads(host.file(POLICY).content_string)
    ext = (BUILD / "extension-id").read_text().strip()
    p = wait_for(lambda: (x := pol()).get("3rdparty") and x, 45, what="watermark policy on the session host")
    force = p["ExtensionInstallForcelist"]
    assert len(force) == 1 and force[0].startswith(f"{ext};") and force[0].endswith("/ext/updates.xml"), force
    assert p["NativeMessagingBlocklist"] == ["*"] and p["NativeMessagingAllowlist"] == ["com.cc.watermark"]
    cat = con.get("/api/catalog")
    start = cat["rings"]["broad"]
    items = next(v for v in cat["versions"] if v["version"] == start)["items"]
    sensitive = {i["url"] for i in items if i.get("sensitive")}
    assert set(p["3rdparty"]["extensions"][ext]["sensitive"]) == sensitive

    # Marking another system sensitive reaches the policy; unmarking removes it.
    other = next(i for i in items if not i.get("sensitive"))
    con.post(f"/api/devices/{cfg['session_hosts'][0]}/ring", {"ring": "pilot"})
    try:
        con.post("/api/catalog", {"items": [dict(i, sensitive=True) if i is other else i for i in items]})
        wait_for(lambda: other["url"] in pol()["3rdparty"]["extensions"][ext]["sensitive"], 45, what="newly sensitive system")
    finally:
        con.post("/api/catalog/rollback", {"version": start})
        con.post(f"/api/devices/{cfg['session_hosts'][0]}/ring", {"ring": "broad"})
    wait_for(lambda: other["url"] not in pol()["3rdparty"]["extensions"][ext]["sensitive"], 45, what="rollback")
