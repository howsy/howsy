#!/usr/bin/env python3
"""Pack a Chrome extension as a signed CRX3 file plus the update manifest Chrome
needs to force-install it from our own server (no Chrome Web Store).

  python3 tools/pack-crx.py services/cc-watermark .lab/watermark-key.pem .lab/watermark \\
      --codebase http://broker-01.lab.example.com:8090/ext/cc-watermark.crx

Writes to the output directory:
  cc-watermark.crx   the signed package
  updates.xml        update manifest pointing at the package
  extension-id       the 32-letter id Chrome derives from the key
  unpacked/          the same files with "key" in manifest.json (same id when loaded unpacked)

The key is created on first use (RSA 2048, mode 0600). Keep it: a new key means a
new extension id. Output is deterministic, so re-running changes nothing.
"""
import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ZIP_DATE = (2020, 1, 1, 0, 0, 0)


def load_key(path):
    if not os.path.exists(path):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    with open(path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def field(num, data):
    """Protobuf length-delimited field."""
    return varint(num << 3 | 2) + varint(len(data)) + data


def extension_id(pub_der):
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(pub_der).hexdigest()[:32])


def zip_dir(src, extra_manifest=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(src):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(root, name)
                rel = os.path.relpath(path, src)
                data = open(path, "rb").read()
                if rel == "manifest.json" and extra_manifest:
                    data = (json.dumps(dict(json.loads(data), **extra_manifest), indent=2) + "\n").encode()
                info = zipfile.ZipInfo(rel, ZIP_DATE)
                info.external_attr = 0o644 << 16
                z.writestr(info, data, zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def pack(src, key):
    pub = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    crx_id = hashlib.sha256(pub).digest()[:16]
    archive = zip_dir(src)
    signed_data = field(1, crx_id)                                   # SignedData.crx_id
    message = b"CRX3 SignedData\x00" + struct.pack("<I", len(signed_data)) + signed_data + archive
    signature = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    proof = field(1, pub) + field(2, signature)                      # AsymmetricKeyProof
    header = field(2, proof) + field(10000, signed_data)            # CrxFileHeader
    crx = b"Cr24" + struct.pack("<II", 3, len(header)) + header + archive
    return crx, pub


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src")
    ap.add_argument("key")
    ap.add_argument("out")
    ap.add_argument("--codebase", required=True, help="URL the .crx is served from")
    ap.add_argument("--name", default="cc-watermark")
    a = ap.parse_args()

    key = load_key(a.key)
    crx, pub = pack(a.src, key)
    ext_id = extension_id(pub)
    version = json.load(open(os.path.join(a.src, "manifest.json")))["version"]
    xml = ("<?xml version='1.0' encoding='UTF-8'?>\n"
           "<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>\n"
           f"  <app appid='{ext_id}'>\n    <updatecheck codebase='{a.codebase}' version='{version}' />\n  </app>\n</gupdate>\n")

    os.makedirs(a.out, exist_ok=True)
    changed = False
    for name, data in ((f"{a.name}.crx", crx), ("updates.xml", xml.encode()), ("extension-id", (ext_id + "\n").encode())):
        path = os.path.join(a.out, name)
        if not os.path.exists(path) or open(path, "rb").read() != data:
            with open(path, "wb") as f:
                f.write(data)
            changed = True
    unpacked = os.path.join(a.out, "unpacked")
    shutil.rmtree(unpacked, ignore_errors=True)
    shutil.copytree(a.src, unpacked)
    with open(os.path.join(unpacked, "manifest.json"), "w") as f:
        json.dump(dict(json.load(open(os.path.join(a.src, "manifest.json"))), key=base64.b64encode(pub).decode()), f, indent=2)
    print(f"{ext_id} {'changed' if changed else 'unchanged'}")


if __name__ == "__main__":
    sys.exit(main())
