#!/usr/bin/env bash
# Simulated call-center lab. Needs only Docker (with Compose v2).
#   ./lab.sh all          start, configure and test everything
#   ./lab.sh up | provision | test | test-fast | golden-image | info | shell | down | clean
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
  HAProxy stats      http://localhost:8404/

Passwords come from .env. The browser will warn about the self-signed certificate.
TXT
}

cmd=${1:-help}; shift || true
case "$cmd" in
  up)           up ;;
  provision)    $CTL make provision ANSIBLE_ARGS="$*" ;;
  test)         $CTL make test PYTEST_ARGS="$*" ;;
  test-fast)    $CTL make test-fast PYTEST_ARGS="$*" ;;
  golden-image) $DC rm -sf ep-office-01 && $DC up -d --wait ep-office-01 && $CTL make golden-provision ;;
  all)          up && $CTL make provision && $CTL make test && info ;;
  info)         info ;;
  shell)        $DC exec controller bash ;;
  down)         $DC down ;;
  clean)        $DC exec -T controller rm -rf /lab/.lab 2>/dev/null || true; $DC down -v --remove-orphans ;;
  *)            sed -n '2,5p' "$0" ;;
esac
