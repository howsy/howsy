# Stand-in for the VPN gateway (design option: WireGuard). Uses the userspace
# wireguard-go implementation so it runs without the kernel module.
FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      wireguard-tools wireguard-go iproute2 iptables iputils-ping \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh gen-keys.sh /
RUN chmod +x /entrypoint.sh /gen-keys.sh
ENTRYPOINT ["/entrypoint.sh"]
