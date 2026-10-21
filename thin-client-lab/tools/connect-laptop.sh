#!/usr/bin/env bash
# Lets one outside machine (a real Windows laptop or a Windows VM) reach the lab's
# corporate network (AD, DNS, broker, RDP farm) through this computer.
#
#   sudo tools/connect-laptop.sh install <laptop-ip-or-subnet> [lan-interface]
#   sudo tools/connect-laptop.sh apply                 (re-applies the rule; run at boot)
#   sudo tools/connect-laptop.sh remove
#
# Docker blocks outside traffic to container addresses by default. This turns on
# the daemon's "allow-direct-routing" and allows only the given laptop address,
# arriving on the LAN interface, into the corp network (10.10.0.0/24).
set -euo pipefail
CORP_NET=cclab_corp
CORP_SUBNET=10.10.0.0/24
STATE=/etc/cclab-laptop-access.conf
UNIT=/etc/systemd/system/cclab-laptop-access.service
SELF=/usr/local/sbin/cclab-laptop-access

die() { echo "error: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || die "run with sudo"

bridge() {
  local id
  id=$(docker network inspect "$CORP_NET" -f '{{.Id}}' 2>/dev/null) || die "lab network $CORP_NET not found: start the lab first"
  echo "br-${id:0:12}"
}

delete_rules() {
  while iptables -S DOCKER-USER 2>/dev/null | grep -q 'cclab-laptop'; do
    rule=$(iptables -S DOCKER-USER | grep 'cclab-laptop' | head -1 | sed 's/^-A /-D /')
    eval iptables $rule
  done
}

apply() {
  [ -f "$STATE" ] || die "not installed"
  . "$STATE"
  local br; br=$(bridge)
  delete_rules
  iptables -I DOCKER-USER -i "$LAN_IF" -o "$br" -s "$LAPTOP" -d "$CORP_SUBNET" \
    -m comment --comment cclab-laptop -j ACCEPT
  echo "allowed $LAPTOP on $LAN_IF -> $CORP_SUBNET ($br)"
}

set_direct_routing() {   # $1 = true|false; restarts Docker if the setting changes
  python3 - "$1" <<'PY'
import json, os, sys
path = "/etc/docker/daemon.json"
conf = json.load(open(path)) if os.path.exists(path) and os.path.getsize(path) else {}
want = sys.argv[1] == "true"
if conf.get("allow-direct-routing", False) == want:
    sys.exit(1)
conf["allow-direct-routing"] = want
os.makedirs("/etc/docker", exist_ok=True)
json.dump(conf, open(path, "w"), indent=2)
PY
}

restart_docker() {
  echo "restarting Docker (the lab containers come back by themselves)"
  systemctl restart docker
  sleep 5
}

case "${1:-}" in
  install)
    LAPTOP=${2:?laptop IP or subnet, e.g. 192.168.1.23 or 192.168.1.0/24}
    LAN_IF=${3:-$(ip -o route get 1.1.1.1 | sed -n 's/.* dev \([^ ]*\).*/\1/p')}
    VM_IP=$(ip -o -4 addr show dev "$LAN_IF" | awk '{print $4}' | cut -d/ -f1 | head -1)
    printf 'LAPTOP=%s\nLAN_IF=%s\n' "$LAPTOP" "$LAN_IF" > "$STATE"
    install -m 755 "$0" "$SELF"
    if set_direct_routing true; then restart_docker; fi
    if command -v systemctl >/dev/null && [ -d /run/systemd/system ]; then
      cat > "$UNIT" <<UNITEOF
[Unit]
Description=Let the test laptop reach the call-center lab
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStartPre=/bin/sleep 10
ExecStart=$SELF apply
RemainAfterExit=true

[Install]
WantedBy=multi-user.target
UNITEOF
      systemctl daemon-reload
      systemctl enable --now cclab-laptop-access.service >/dev/null
    fi
    apply
    cat <<TXT

On the Windows laptop (PowerShell as administrator, in the repo's windows folder):

  .\\Connect-LabLaptop.ps1 -VmIp $VM_IP

TXT
    ;;
  apply)
    apply ;;
  remove)
    delete_rules
    rm -f "$STATE"
    if [ -f "$UNIT" ]; then systemctl disable --now cclab-laptop-access.service >/dev/null 2>&1 || true; rm -f "$UNIT"; fi
    if set_direct_routing false; then restart_docker; fi
    echo "laptop access removed" ;;
  *)
    sed -n '2,12p' "$0" ;;
esac
