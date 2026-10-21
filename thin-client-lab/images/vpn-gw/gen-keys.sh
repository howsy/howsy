#!/bin/sh
# Generates the gateway key pair and one key pair per home endpoint into /keys.
set -eu
umask 077
cd /keys
for name in gateway "$@"; do
  [ -f "$name.key" ] || wg genkey > "$name.key"
  wg pubkey < "$name.key" > "$name.pub"
done
