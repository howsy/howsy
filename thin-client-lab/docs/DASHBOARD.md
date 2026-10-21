# Management console

One place to run the call-center fleet: every agent laptop, session host, endpoint and the broker.
It reports what each device is doing, checks that each one is set up correctly, and lets IT act on
it. In the lab it runs on `broker-01` and opens at **http://localhost:8090/**. Sign in with
`itadmin1` / `It-Admin-Pass1!`. Only members of the AD group **CC-Admins** can sign in.

| Overview | Device page | Live view of an agent's desktop |
|---|---|---|
| ![](screenshots/console-overview.png) | ![](screenshots/console-device.png) | ![](screenshots/console-live.png) |

## What it does, and where the ideas come from

| Feature | Console page | Borrowed from |
|---|---|---|
| Inventory: every device, its role, OS, user, health, last check-in | Devices | Intune device list |
| **Compliance checks** per device: AD-joined, SSSD, security updates; on session hosts xrdp, open-on-laptop relay, per-agent CPU/RAM caps, kiosk mode, terminal restricted; on endpoints USB storage blocked, screen lock, browser policy, no admin rights, VPN, disk encryption. Failures make the device *Not compliant*; pending updates only warn. | Device → Compliance | Intune compliance policies |
| **Remote actions**: refresh, send message, collect logs, restart service, re-apply desktop, reboot; for laptops: start Genesys, reconnect desktop, sign out | Device → Actions | Intune remote actions |
| **See what happens on the device**: a live view of the agent's desktop or of the laptop screen (refreshed every 2 s), and a timeline of events (logged in, desktop opened, Genesys started, came online/offline, compliance changed, catalog applied, what admins did) | Device → View screen / Watch, Timeline | Intune Remote Help, device timeline |
| **Sessions**: every agent desktop on the farm, the laptop it is viewed from, Genesys running or not, CPU/RAM per agent; Watch, Message, Log off | Sessions | VDI session monitoring |
| **Apps & desktop**: the list of web systems on the agent desktop (one icon, one browser tab and one bookmark each); edit it in the browser, publish, and the session hosts update within ~15 s, including desktops already open | Apps & desktop | Intune app assignment |
| **Rings**: a published catalog goes to the *pilot* ring first; *Promote* sends it to *broad*. Every version is kept and can be rolled back with one click. | Apps & desktop | Intune update rings, SteamOS beta channel and A/B rollback |
| **Capacity**: CPU history per session host, desktops per host, alerts at high CPU/RAM | Overview | — |
| **Alerts**: offline servers (critical), offline devices, not-compliant devices, overloaded hosts | Overview | — |
| **Audit log** of every sign-in, action, screen view, publish, promote and rollback | Activity → Admin audit log | Intune audit logs |

## Privacy and safety

- When an admin starts watching an agent's desktop, the agent gets an on-screen notice: *"An
  administrator is viewing your screen."* Every view is in the audit log. Check this against your
  local rules and works-council agreements before the pilot.
- Devices only run actions from a fixed list (`ACTIONS` in `services/cc_console.py`). There is no
  "run any command" button. Services that can be restarted are allow-listed per role.
- Admin sign-in is checked against AD (Kerberos) and the CC-Admins group. The session is an
  HttpOnly, SameSite=Strict cookie, every change needs a CSRF header, and 5 failed sign-ins from an
  address lock it out for 5 minutes.
- Linux machines authenticate with an enrollment token. Laptops authenticate with the agent's own
  Kerberos ticket, so a laptop can never claim to be another user.
- Production: serve the console over HTTPS (`tls_cert`/`tls_key` in `console.json`), keep it on the
  internal network only, and keep the enrollment token in Ansible Vault.

## How it works

```
 session hosts, endpoints, broker            agent laptops (Windows: CC-Shell.ps1)
   cc-device-agent (root, every 15 s)           check-in with Kerberos as the agent
          │  health, compliance, sessions,              │  desktop open?, Genesys running?,
          │  events; waits for commands                 │  events; waits for commands
          ▼                                             ▼
                cc-console  (broker-01:8090, SQLite)  ◀── admins in a browser (AD sign-in)
                  devices · events · commands · catalog versions · rings · audit
```

- `services/cc_console.py` is the server and API. `services/console-ui/` is the web app (no
  internet needed).
- `services/cc-device-agent.py` runs on Linux machines. On session hosts it also applies the catalog
  for its ring with `cc-desktop-render`.
- `services/cc-laptop-agent.py` (the lab's stand-in laptop) and `windows/CC-Shell.ps1` (real laptops)
  report as role *laptop*.
- Ansible roles: `console` (on brokers) and `device_agent` (everywhere else).

## What it does not replace

For the **Windows laptops' own operating system**, use Microsoft Intune (or your current tool such
as SCCM) if you have it. That covers Windows Update rings, BitLocker escrow, Defender, the Genesys
app installation and Autopilot. This console doesn't touch the Windows OS. It manages the
call-center layer on top: who is working where, the agent desktop, the farm, and support. The
laptop shell reports only while an agent is logged in. For laptops that are switched on but logged
out, Intune or a small SYSTEM-level agent is needed.

The Linux side (session hosts, broker, Linux endpoints) is fully covered by this console plus
Ansible.

## Tests

`tests/test_12_console.py` checks all of the above in the simulated lab:
- only CC-Admins can sign in; the CSRF header is required; bad device tokens are refused
- every machine reports, and the servers are compliant
- sessions are linked to laptops
- watching an agent desktop and the laptop screen returns real screenshots, and the view is audited
- message and Start Genesys work
- the timeline is filled
- only allow-listed actions run
- log off works
- pilot → promote → rollback changes exactly the right hosts
