# Stand-in for the existing Active Directory: a Samba 4 AD domain controller.
FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      samba samba-ad-dc samba-ad-provision krb5-user winbind ldb-tools dnsutils ldap-utils \
 && rm -rf /var/lib/apt/lists/* /etc/samba/smb.conf
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 53/udp 53 88 88/udp 389 445 464 636
HEALTHCHECK --interval=5s --timeout=5s --start-period=60s --retries=30 \
  CMD test -f /run/dc-seeded && ldapsearch -LLL -x -H ldap://127.0.0.1 -b "" -s base defaultNamingContext >/dev/null
ENTRYPOINT ["/entrypoint.sh"]
