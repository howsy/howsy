#!/usr/bin/env bash
# Simulated call-center lab. Needs only Docker (with Compose v2).
#   ./lab.sh all          start, configure and test everything
#   ./lab.sh up | provision | test | test-fast | golden-image | info | shell | stop | start | down | clean
#   ./lab.sh watch-laptop [user]   live view of what the agent's laptop is told to do
#   ./lab.sh connect-laptop <ip>   let a real Windows laptop (or Windows VM) at <ip> reach the lab
#   ./lab.sh disconnect-laptop
set -euo pipefail
cd "$(dirname "$0")"
DC="docker compose"
CTL="$DC exec -T controller"

up() {
  [ -f .env ] || cp .env.example .env
  docker image inspect cclab/managed-node >/dev/null 2>&1 || docker build -t cclab/managed-node images/managed-node
  $DC up -d --build controller
  $CTL make prereqs
  $DC up -d --build --wait --wait-timeout 600
}

info() {
  cat <<'TXT'

Lab is running. Try it like an agent would:

  Remote portal      https://localhost:8443/
                     user agent1 / Agent-Pass1!   (scan the QR code with any authenticator app)
                     user outsider1 is a real AD account that must be refused
  Session desktop    Remote Desktop to localhost:13389  (agent1 / Agent-Pass1!)
                     run "./lab.sh watch-laptop" in another terminal, then double-click
                     "Genesys Cloud" on that desktop: the laptop starts / focuses Genesys
  HAProxy stats      http://localhost:8404/
  Management console http://localhost:8090/   (itadmin1 / It-Admin-Pass1!)

Passwords come from .env. The browser will warn about the self-signed certificate.
TXT
}

cmd=${1:-help}; shift || true
case "$cmd" in
  up)           up ;;
  provision)    $CTL make provision ANSIBLE_ARGS="$*" ;;
  test)         $CTL make -s ssh-key && $CTL python3 -m pytest "$@" ;;
  test-fast)    $CTL make -s ssh-key && $CTL python3 -m pytest -m "not slow" "$@" ;;
  golden-image) $DC rm -sf ep-office-01 && $DC up -d --wait ep-office-01 && $CTL make golden-provision ;;
  all)          up && $CTL make provision && $CTL make test && info ;;
  info)         info ;;
  watch-laptop) user=${1:-agent1}; pw=$(sed -n 's/^AGENT_PASSWORD=//p' .env)
                $DC exec ep-office-01 su - "$user" -c "printf '%s' '$pw' | kinit $user >/dev/null && cc-laptop-listen" ;;
  shell)        $DC exec controller bash ;;
  connect-laptop)    sudo tools/connect-laptop.sh install "$@" ;;
  disconnect-laptop) sudo tools/connect-laptop.sh remove ;;
  stop)         $DC stop ;;                       # pause, keeps everything configured
  start)        $DC start && info ;;
  down)         $DC down ;;                       # removes the machines: run provision again after up
  clean)        $DC exec -T controller rm -rf /lab/.lab 2>/dev/null || true; $DC down -v --remove-orphans ;;
  *)            sed -n '2,7p' "$0" ;;
esac
