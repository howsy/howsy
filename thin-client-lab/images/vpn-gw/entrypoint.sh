#!/bin/bash
# WireGuard gateway: wg0 = 10.99.0.1/24. Home endpoints reach only the corporate
# subnet through the tunnel; nothing is forwarded from the internet side otherwise.
set -euo pipefail
export WG_QUICK_USERSPACE_IMPLEMENTATION=wireguard-go
export WG_I_PREFER_BUGGY_USERSPACE_TO_POLISHED_KMOD=1
CORP_SUBNET=${CORP_SUBNET:-10.10.0.0/24}
TUNNEL_SUBNET=${TUNNEL_SUBNET:-10.99.0.0/24}

until [ -f /keys/gateway.key ]; do echo "waiting for /keys/gateway.key"; sleep 2; done
{
  echo "[Interface]"
  echo "Address = 10.99.0.1/24"
  echo "ListenPort = 51820"
  echo "PrivateKey = $(cat /keys/gateway.key)"
  n=10
  for pub in /keys/*.pub; do
    [ "$(basename "$pub")" = gateway.pub ] && continue
    echo; echo "[Peer]  # $(basename "$pub" .pub)"
    echo "PublicKey = $(cat "$pub")"
    echo "AllowedIPs = 10.99.0.$n/32"
    n=$((n+1))
  done
} > /etc/wireguard/wg0.conf
chmod 600 /etc/wireguard/wg0.conf

CORP_IF=$(ip -o route get "${CORP_SUBNET%/*}" | sed -n 's/.* dev \([^ ]*\).*/\1/p')
iptables -P FORWARD DROP
iptables -A FORWARD -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A FORWARD -i wg0 -o "$CORP_IF" -s "$TUNNEL_SUBNET" -d "$CORP_SUBNET" -j ACCEPT
iptables -t nat -A POSTROUTING -s "$TUNNEL_SUBNET" -o "$CORP_IF" -j MASQUERADE

wg-quick up wg0
echo ">> vpn gateway up"; wg show
trap 'wg-quick down wg0; exit 0' TERM INT
sleep infinity & wait
