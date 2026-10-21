#!/bin/bash
set -euo pipefail
PORTAL_NAME=${PORTAL_NAME:-remote.lab.example.com}
mkdir -p /etc/nginx/tls
if [ ! -f /etc/nginx/tls/portal.crt ]; then
  # Lab: self-signed. Production: a real certificate for the public DNS name.
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 -subj "/CN=$PORTAL_NAME" \
    -addext "subjectAltName=DNS:$PORTAL_NAME" \
    -keyout /etc/nginx/tls/portal.key -out /etc/nginx/tls/portal.crt 2>/dev/null
fi
touch /var/log/nginx/access.log /var/log/nginx/error.log
mkdir -p /run/fail2ban
# Addresses never banned (e.g. the lab test controller). Empty in production.
printf '[DEFAULT]\nignoreip = 127.0.0.1/8 ::1 %s\n' "${F2B_IGNOREIP:-}" > /etc/fail2ban/jail.d/00-ignoreip.local
fail2ban-client -x start
nginx -t
exec nginx -g 'daemon off;'
