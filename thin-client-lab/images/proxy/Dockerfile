# DMZ reverse proxy in front of Guacamole: TLS, rate limiting and fail2ban.
FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      nginx fail2ban iptables openssl python3-systemd \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default /etc/fail2ban/jail.d/defaults-debian.conf
COPY nginx.conf /etc/nginx/conf.d/portal.conf
COPY fail2ban/filter.d/ /etc/fail2ban/filter.d/
COPY fail2ban/jail.d/ /etc/fail2ban/jail.d/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
EXPOSE 80 443
ENTRYPOINT ["/entrypoint.sh"]
