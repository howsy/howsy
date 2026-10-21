#!/usr/bin/env python3
"""Size the on-prem server(s) that host the VMs, from per-user CPU and RAM.

The design is on-prem: one (or a small pair of) physical server(s) running
Proxmox hosts every VM in the lab table -- the AD DC, the file server, the
session hosts the agents actually work on, HAProxy, the Guacamole portal, the
DMZ proxy and the VPN gateway. The agents keep the laptops they already have;
those laptops only draw the server desktop (option A) or run a locked-down
Linux browser endpoint (option B), so nothing heavy runs on them. This tool
answers the two questions that follow from that:

  forward  "For N agents, how big must the server be?"
  reverse  "I have this server already -- how many agents fit?"  (--server-*)

The only numbers that matter are CPU and RAM *per logged-in user* on a session
host. Measure them during the pilot with tools/sessionhost_usage.py and feed
them in; until then the defaults below are placeholders, not measurements.

  # placeholders, 200 agents
  python3 tools/capacity_sizing.py --agents 200

  # real pilot numbers (RAM in MB/user, CPU in % of one core/user,
  # i.e. the MB/user and CPU%/user columns that sessionhost_usage.py prints)
  python3 tools/capacity_sizing.py --agents 200 --ram-mb 1900 --cpu-pct 22

  # read those two numbers straight from a saved sessionhost_usage.py run
  python3 tools/sessionhost_usage.py > pilot.txt      # a few times per shift
  python3 tools/capacity_sizing.py --agents 200 --from-usage pilot.txt

  # reverse: how many agents fit on a server you already own?
  python3 tools/capacity_sizing.py --server-cores 32 --server-ram-gb 256
"""
import argparse
import math
import sys

# Fixed infrastructure VMs that run regardless of how many agents there are,
# one row per VM in the design's lab table (plus the usual production extras).
# vCPU and RAM (GB) are starting points -- edit to match what you actually give
# each VM. These do not grow with the agent count; the session hosts do.
INFRA_VMS = {
    "ad-dc":          (2, 4),   # Active Directory domain controller
    "files-nfs":      (2, 4),   # file server, /home over NFS+Kerberos
    "haproxy-a":      (1, 1),   # load balancer      } keepalived VIP pair
    "haproxy-b":      (1, 1),   # load balancer      }
    "guacamole":      (2, 4),   # guacd + guac + its Postgres (portal)
    "dmz-proxy":      (1, 1),   # nginx + fail2ban in the DMZ
    "vpn-gw":         (1, 1),   # WireGuard gateway for home agents
    "broker":         (1, 1),   # SSO broker for the laptop viewer (option A)
    "monitoring":     (2, 4),   # Zabbix
    "mgmt-console":   (1, 2),   # the management console
}


def ceil_to(value, step):
    """Round value up to the next multiple of step (for a buyable server)."""
    return int(math.ceil(value / step) * step)


def read_usage(path):
    """Pull the busiest MB/user and CPU%/user out of a sessionhost_usage.py
    capture. Its columns are: host users MB/user CPU%/user host-RAM load1 cores.
    We size for the worst row seen, not the average."""
    ram_mb = cpu_pct = 0.0
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("host"):
                continue
            parts = line.split()
            # host users MB/user CPU%/user ... -> indices 2 and 3
            try:
                ram_mb = max(ram_mb, float(parts[2]))
                cpu_pct = max(cpu_pct, float(parts[3]))
            except (IndexError, ValueError):
                continue
    if not ram_mb:
        sys.exit(f"{path}: no usable rows (run tools/sessionhost_usage.py into it first)")
    return ram_mb, cpu_pct


def infra_totals():
    vcpu = sum(c for c, _ in INFRA_VMS.values())
    ram_gb = sum(r for _, r in INFRA_VMS.values())
    return vcpu, ram_gb


def forward(a):
    """How big must the server be for a.agents agents?"""
    concurrent = math.ceil(a.agents * a.concurrency)

    # Agent desktops on the session hosts.
    pool_ram_gb = concurrent * a.ram_mb / 1024
    pool_vcpu = concurrent * (a.cpu_pct / 100.0)   # % of one core -> vCPU

    # Session-host VMs. RAM is never oversubscribed for VDI, so host count is
    # driven by whichever is tighter: the per-host user cap or per-host RAM.
    hosts_by_cap = math.ceil(concurrent / a.max_users_per_host)
    hosts_by_ram = math.ceil(pool_ram_gb / (a.host_ram_gb * (1 - a.ram_headroom)))
    active_hosts = max(1, hosts_by_cap, hosts_by_ram)
    total_hosts = active_hosts + (1 if a.redundant_host else 0)  # N+1 failover

    # Infra.
    infra_vcpu, infra_ram_gb = infra_totals()

    # Physical server. RAM: everything running at once, never oversubscribed,
    # plus a headroom margin and the hypervisor's own reservation. CPU: vCPU
    # may be oversubscribed (light, bursty call-center load) by --cpu-overcommit.
    guest_ram_gb = pool_ram_gb + infra_ram_gb
    server_ram_gb = guest_ram_gb / (1 - a.ram_headroom) + a.host_reserve_ram_gb

    guest_vcpu = pool_vcpu + infra_vcpu
    server_cores = guest_vcpu / a.cpu_overcommit + a.host_reserve_cores

    rec_cores = ceil_to(server_cores, 4)
    rec_ram_gb = ceil_to(server_ram_gb, 32)

    print(f"On-prem server sizing for {a.agents} agents "
          f"({int(a.concurrency*100)}% peak = {concurrent} logged in at once)")
    print(f"per user: {a.ram_mb:.0f} MB, {a.cpu_pct:.0f}% of one core "
          f"({'measured' if a.measured else 'PLACEHOLDER -- measure with sessionhost_usage.py'})")
    print()
    print("Agent desktop pool (on the session hosts)")
    print(f"  RAM demand            {pool_ram_gb:>8.0f} GB")
    print(f"  vCPU demand           {pool_vcpu:>8.0f} vCPU")
    print(f"  session-host VMs      {total_hosts:>8}  "
          f"({active_hosts} active + {total_hosts-active_hosts} for failover, "
          f"<={a.max_users_per_host} users each)")
    print()
    print(f"Infrastructure VMs ({len(INFRA_VMS)})")
    print(f"  RAM                   {infra_ram_gb:>8} GB")
    print(f"  vCPU                  {infra_vcpu:>8} vCPU")
    print()
    print("Physical server total")
    print(f"  RAM (guests {guest_ram_gb:.0f} GB + {int(a.ram_headroom*100)}% headroom "
          f"+ {a.host_reserve_ram_gb} GB hypervisor)")
    print(f"      need              {server_ram_gb:>8.0f} GB   -> buy {rec_ram_gb} GB")
    print(f"  CPU (guests {guest_vcpu:.0f} vCPU / {a.cpu_overcommit:g}:1 overcommit "
          f"+ {a.host_reserve_cores} cores hypervisor)")
    print(f"      need              {server_cores:>8.0f} cores -> buy {rec_cores} cores")
    print()
    if a.two_servers:
        print("Redundancy: size each of TWO servers to carry the whole load if its")
        print("partner is down (above is one server's full-load figure). Split the VMs")
        print("across both for day to day; live-migrate on failure or maintenance.")
    else:
        print("One physical server shown (the spare session host above is an extra VM")
        print("on it, not a second box). That server is still a single point of")
        print("failure; for none, pass --two-servers to size an HA pair.")
    print()
    print("Agent devices: NOT replaced. Each endpoint only draws the server")
    print("desktop (option A) or runs a locked-down browser (option B): ~2 cores,")
    print("~4 GB, any laptop from the last decade. Keep the laptops you have; the")
    print("spend is this server, not a device refresh.")


def reverse(a):
    """How many agents fit on a server you already have?"""
    infra_vcpu, infra_ram_gb = infra_totals()

    avail_ram_gb = (a.server_ram_gb - a.host_reserve_ram_gb) * (1 - a.ram_headroom) - infra_ram_gb
    avail_vcpu = (a.server_cores - a.host_reserve_cores) * a.cpu_overcommit - infra_vcpu
    if avail_ram_gb <= 0 or avail_vcpu <= 0:
        sys.exit("This server is too small to host even the infrastructure VMs with headroom.")

    by_ram = int(avail_ram_gb * 1024 / a.ram_mb)
    by_cpu = int(avail_vcpu / (a.cpu_pct / 100.0))
    concurrent = min(by_ram, by_cpu)
    agents = int(concurrent / a.concurrency)
    bound = "RAM" if by_ram < by_cpu else "CPU"

    print(f"Capacity of a {a.server_cores}-core / {a.server_ram_gb} GB server")
    print(f"per user: {a.ram_mb:.0f} MB, {a.cpu_pct:.0f}% of one core "
          f"({'measured' if a.measured else 'PLACEHOLDER -- measure with sessionhost_usage.py'})")
    print()
    print(f"  after infra + {int(a.ram_headroom*100)}% headroom + hypervisor reserve:")
    print(f"    RAM supports        {by_ram:>6} concurrent users")
    print(f"    CPU supports        {by_cpu:>6} concurrent users  (at {a.cpu_overcommit:g}:1)")
    print(f"  concurrent users      {concurrent:>6}   (bound by {bound})")
    print(f"  total agents          {agents:>6}   (at {int(a.concurrency*100)}% peak concurrency)")
    print()
    print("Those agents keep their current laptops as thin endpoints -- no refresh.")


def main():
    p = argparse.ArgumentParser(
        description="Size the on-prem VM server from per-user CPU/RAM, or find its agent capacity.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--agents", type=int, help="total agents to support (forward sizing)")
    p.add_argument("--server-cores", type=int, help="physical cores of a server you already have (reverse)")
    p.add_argument("--server-ram-gb", type=int, help="RAM (GB) of that server (reverse)")

    p.add_argument("--ram-mb", type=float, default=1800, help="RAM per logged-in user, MB")
    p.add_argument("--cpu-pct", type=float, default=18, help="CPU per logged-in user, %% of one core")
    p.add_argument("--from-usage", metavar="FILE", help="read --ram-mb/--cpu-pct (worst row) from a sessionhost_usage.py capture")

    p.add_argument("--concurrency", type=float, default=0.9, help="fraction of agents logged in at peak")
    p.add_argument("--ram-headroom", type=float, default=0.2, help="RAM kept free (0.2 = run to 80%%)")
    p.add_argument("--cpu-overcommit", type=float, default=3.0, help="vCPU:pCPU ratio (call-center load is light/bursty)")
    p.add_argument("--max-users-per-host", type=int, default=50, help="soft cap of users per session-host VM")
    p.add_argument("--host-ram-gb", type=int, default=64, help="RAM per session-host VM")
    p.add_argument("--no-redundant-host", dest="redundant_host", action="store_false", help="drop the N+1 spare session host")
    p.add_argument("--two-servers", action="store_true", help="size for a 2-server HA pair (each carries all load)")
    p.add_argument("--host-reserve-cores", type=int, default=2, help="cores reserved for the hypervisor")
    p.add_argument("--host-reserve-ram-gb", type=int, default=8, help="RAM (GB) reserved for the hypervisor")
    a = p.parse_args()

    a.measured = False
    if a.from_usage:
        a.ram_mb, a.cpu_pct = read_usage(a.from_usage)
        a.measured = True
    elif "--ram-mb" in sys.argv or "--cpu-pct" in sys.argv:
        a.measured = True

    reverse_mode = a.server_cores is not None or a.server_ram_gb is not None
    if reverse_mode:
        if not (a.server_cores and a.server_ram_gb):
            p.error("reverse sizing needs both --server-cores and --server-ram-gb")
        reverse(a)
    elif a.agents:
        forward(a)
    else:
        p.error("give --agents (forward) or --server-cores and --server-ram-gb (reverse)")


if __name__ == "__main__":
    main()
