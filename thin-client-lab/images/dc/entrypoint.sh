#!/bin/bash
# Provisions the lab domain on first start (step 1 of the lab build: test OU,
# test users, CC-Agents-Test group, Guacamole LDAP service account), then runs samba.
set -euo pipefail

REALM=${REALM:-LAB.EXAMPLE.COM}
NETBIOS=${NETBIOS:-LAB}
DOMAIN=${REALM,,}
BASE_DN="DC=${DOMAIN//./,DC=}"
OU="OU=CallCenterTest,${BASE_DN}"
: "${ADMIN_PASSWORD:?}" "${AGENT_PASSWORD:?}" "${SVC_GUAC_PASSWORD:?}"

if [ ! -f /var/lib/samba/private/sam.ldb ]; then
  echo ">> provisioning ${REALM}"
  rm -f /etc/samba/smb.conf
  samba-tool domain provision --realm="$REALM" --domain="$NETBIOS" \
    --server-role=dc --dns-backend=SAMBA_INTERNAL --host-name=dc01 \
    --adminpass="$ADMIN_PASSWORD" --use-rfc2307 \
    --option="dns forwarder = 127.0.0.11"
  # Lab only: Guacamole binds with plain LDAP. Production should use LDAPS (636).
  sed -i '/^\[global\]/a \\tldap server require strong auth = no' /etc/samba/smb.conf
  # Lab only: allow immediate password changes (production keeps the domain policy).
  samba-tool domain passwordsettings set --min-pwd-age=0 --max-pwd-age=0 --history-length=0

  samba-tool ou create "$OU"
  samba-tool ou create "OU=Computers,$OU"
  samba-tool group add CC-Agents-Test --groupou="OU=CallCenterTest"
  for u in agent1 agent2 agent3; do
    samba-tool user create "$u" "$AGENT_PASSWORD" --userou="OU=CallCenterTest" \
      --given-name="Test" --surname="$u" --mail-address="$u@$DOMAIN"
  done
  samba-tool group addmembers CC-Agents-Test agent1,agent2,agent3
  # Valid AD account that is NOT in CC-Agents-Test: must be refused everywhere.
  samba-tool user create outsider1 "$AGENT_PASSWORD" --userou="OU=CallCenterTest"
  # Read-only bind account for Guacamole LDAP lookups.
  samba-tool user create svc-guac "$SVC_GUAC_PASSWORD" --userou="OU=CallCenterTest"
  for u in agent1 agent2 agent3 outsider1 svc-guac; do samba-tool user setexpiry "$u" --noexpiry; done
fi
cp /var/lib/samba/private/krb5.conf /etc/krb5.conf

samba --foreground --no-process-group &
SAMBA_PID=$!

# DNS records for lab services (idempotent).
for i in $(seq 60); do
  samba-tool dns zonelist 127.0.0.1 -U Administrator --password="$ADMIN_PASSWORD" >/dev/null 2>&1 && break
  sleep 2
done
add_a() {
  samba-tool dns query 127.0.0.1 "$DOMAIN" "$1" A -U Administrator --password="$ADMIN_PASSWORD" >/dev/null 2>&1 \
    || samba-tool dns add 127.0.0.1 "$DOMAIN" "$1" A "$2" -U Administrator --password="$ADMIN_PASSWORD"
}
add_a lb      10.10.0.30   # HAProxy virtual IP (RDP)
add_a guac    10.10.0.40
add_a files   10.10.0.20
add_a crm     10.10.0.80   # sample internal web app from the app inventory
add_a vpn     10.10.0.60
add_a broker-01 10.10.0.25 # cc-broker (SSO tickets, open-on-laptop)
# IT admins who may use the management console (added to existing domains too).
ITADMIN_PASSWORD=${ITADMIN_PASSWORD:-It-Admin-Pass1!}
samba-tool group show CC-Admins >/dev/null 2>&1 || samba-tool group add CC-Admins --groupou="OU=CallCenterTest"
if ! samba-tool user show itadmin1 >/dev/null 2>&1; then
  samba-tool user create itadmin1 "$ITADMIN_PASSWORD" --userou="OU=CallCenterTest" --given-name=IT --surname=Admin
  samba-tool user setexpiry itadmin1 --noexpiry
  samba-tool group addmembers CC-Admins itadmin1
fi
# More IT roles for the console (Owner = CC-Admins, Helpdesk, read-only Viewers).
ensure_user() {   # ensure_user <name> <password> <group>
  samba-tool user show "$1" >/dev/null 2>&1 || {
    samba-tool user create "$1" "$2" --userou="OU=CallCenterTest"
    samba-tool user setexpiry "$1" --noexpiry
  }
  samba-tool group show "$3" >/dev/null 2>&1 || samba-tool group add "$3" --groupou="OU=CallCenterTest"
  samba-tool group listmembers "$3" | grep -qx "$1" || samba-tool group addmembers "$3" "$1"
}
ensure_user helpdesk1 "${HELPDESK_PASSWORD:-Helpdesk-Pass1!}" CC-Helpdesk
ensure_user viewer1 "${VIEWER_PASSWORD:-Viewer-Pass1!}" CC-Viewers
# A second owner: promoting a change to every device needs a different admin than the one who published it.
ensure_user itadmin2 "$ITADMIN_PASSWORD" CC-Admins

# svc-console may change the membership of the agents group, and nothing else
# (the console's People page). WP on the "member" attribute of that one group.
if ! samba-tool user show svc-console >/dev/null 2>&1; then
  samba-tool user create svc-console "${SVC_CONSOLE_PASSWORD:-Svc-Console-Pass1!}" --userou="OU=CallCenterTest"
  samba-tool user setexpiry svc-console --noexpiry
fi
SVC_SID=$(samba-tool user show svc-console --attributes=objectSid | sed -n 's/^objectSid: //p')
AGENTS_DN="CN=CC-Agents-Test,$OU"
if ! samba-tool dsacl get --objectdn="$AGENTS_DN" -H ldap://127.0.0.1 -U Administrator --password="$ADMIN_PASSWORD" 2>/dev/null | grep -q "$SVC_SID"; then
  samba-tool dsacl set --objectdn="$AGENTS_DN" -H ldap://127.0.0.1 -U Administrator --password="$ADMIN_PASSWORD" \
    --sddl="(OA;;WP;bf9679c0-0de6-11d0-a285-00aa003049e2;;$SVC_SID)"
fi
touch /run/dc-seeded
echo ">> domain controller ready"
wait "$SAMBA_PID"
