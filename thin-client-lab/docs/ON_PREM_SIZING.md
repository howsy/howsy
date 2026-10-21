# On-prem server: what hosts the VMs, and how big it has to be

This design is **on-prem**. There is no cloud desktop and no per-seat SaaS. One
physical server (or a pair, for no single point of failure) sits in your comms
room running **Proxmox**, and every VM in the lab table runs on it:

```
                          ┌──────────────────────────────────────────┐
                          │  Proxmox server (on-prem)                  │
                          │                                            │
   agents' existing       │   sh-01  sh-02 … session hosts (desktops)  │
   laptops, unchanged ────┼──▶ guacamole portal   HAProxy pair         │
   (thin clients)         │   AD DC   files/NFS   DMZ proxy   VPN gw    │
                          │   Zabbix  mgmt console  broker             │
                          └──────────────────────────────────────────┘
```

The agents **keep the laptops they already have**. A laptop is only a window
onto a desktop that runs on the server — either the Windows viewer (option A,
the laptop shows the server desktop) or a locked-down Linux browser endpoint
(option B). Neither runs Genesys, Chrome-with-20-tabs or the line-of-business
apps locally; the server does. So the project spend is **this server**, not a
laptop refresh. See the two options in the [main README](../README.md).

## The only inputs that matter: CPU and RAM per user

Everything else (host count, how many servers, how much RAM to buy) falls out of
two measured numbers:

- **RAM per logged-in user** on a session host (resident memory, MB)
- **CPU per logged-in user** (percent of one core, averaged at the busy time)

The pilot measures them with [`tools/sessionhost_usage.py`](../tools/sessionhost_usage.py).
Run it a few times per shift during the pilot and keep the busiest reading.
**Do not size production off a guess** — a call-center desktop that sits on one
CRM tab is a very different number from one running a soft-phone, Chrome with a
dozen tabs and a PDF viewer.

## The calculator

[`tools/capacity_sizing.py`](../tools/capacity_sizing.py) turns those two numbers
into a server spec, and back again.

**Forward — "how big a server for N agents?"**

```bash
# feed last shift's worst reading straight in
python3 tools/sessionhost_usage.py > pilot.txt
python3 tools/capacity_sizing.py --agents 200 --from-usage pilot.txt
```

or pass the numbers directly:

```bash
python3 tools/capacity_sizing.py --agents 200 --ram-mb 1900 --cpu-pct 22
```

It prints the agent-desktop pool (RAM/vCPU and how many session-host VMs, with
an N+1 spare), the fixed infrastructure VMs, and the physical server total
rounded up to something you can actually buy.

**Reverse — "I already have this server, how many agents fit?"**

```bash
python3 tools/capacity_sizing.py --server-cores 32 --server-ram-gb 256
```

It reports the concurrent-user ceiling and which resource (almost always RAM)
is the bottleneck.

## How the numbers are built

| Quantity | How it is derived |
|---|---|
| Concurrent users | `agents × --concurrency` (default 0.9: 9 in 10 logged in at peak) |
| Agent pool RAM | `concurrent × RAM-per-user`. **Never oversubscribed** — VDI that swaps is VDI that freezes. |
| Agent pool vCPU | `concurrent × CPU-per-user`. Call-center load is light and bursty, so vCPU **is** oversubscribed, `--cpu-overcommit` (default 3:1). |
| Session-host VMs | enough to hold the pool RAM at `--host-ram-gb` each, and at most `--max-users-per-host`, **+1 for failover** (matches `test_06`). |
| Infrastructure VMs | fixed table in the script (AD, NFS, HAProxy ×2, Guacamole, DMZ proxy, VPN, broker, Zabbix, console). Does not grow with agents. |
| Server RAM | `(pool + infra) ÷ (1 − --ram-headroom) + hypervisor reserve`, rounded up to 32 GB. |
| Server cores | `(pool + infra) vCPU ÷ overcommit + hypervisor reserve`, rounded up to 4. |

Every assumption is a flag — run `python3 tools/capacity_sizing.py --help` to
see and change them. RAM is the number that costs money and the one that cannot
be oversubscribed, so it is almost always what bounds the box.

## Redundancy

The default output is **one** server and is a single point of failure. For none,
size an HA pair:

```bash
python3 tools/capacity_sizing.py --agents 200 --from-usage pilot.txt --two-servers
```

That sizes **each** of two servers to carry the whole load alone, so either can
be patched or can die with the other taking over (Proxmox live-migration / HA).
Day to day the VMs are split across both. This is separate from the N+1 *session
host* (an extra VM so one host can fail without dropping its users to no desktop);
Proxmox, PXE, Zabbix and backups are checked by hand per the design (`I-1`).

## Worked example (placeholder numbers)

200 agents, nothing measured yet, defaults (1800 MB / 18% of a core per user):

```
Agent desktop pool:   316 GB RAM, 32 vCPU, 8 session-host VMs (7 + 1 spare)
Infrastructure VMs:    23 GB RAM, 14 vCPU
Physical server:      ~432 GB RAM  -> buy 448 GB
                      ~17 cores    -> buy 20 cores
```

A single 2-socket box with ~512 GB and 2×16-core CPUs covers it with room to
spare; for HA, two such boxes with `--two-servers`. **Replace these placeholders
with the pilot's measured numbers before ordering hardware.** The endpoint side
stays at ~2 cores / 4 GB whatever the agent count — which is why the laptops you
have are enough.
