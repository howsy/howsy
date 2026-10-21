# Windows laptops as viewers

This setup keeps your current laptops and current Windows image. Agents log in with AD as usual,
but instead of the Windows desktop they get only the call-center desktop from the server farm. The
Genesys Cloud **desktop app** runs on the laptop, so voice stays local. Its icon lives on the server
desktop, and clicking it starts the app on the laptop or brings it to the front.

```
 Windows laptop (AD-joined)                         Server farm (Linux)
 ┌───────────────────────────────┐  Kerberos   ┌──────────────┐
 │ AD login                      │────────────▶│ cc-broker    │ one-time ticket
 │ CC-Shell.ps1 (no explorer)    │◀────────────│              │
 │  ├─ mstsc full screen/docked ─┼── RDP ─────▶│ HAProxy ─▶ sh-01/sh-02 (xrdp + XFCE)
 │  │                            │             │   desktop: icon per web system,
 │  │                            │             │   Chrome with every system as a tab,
 │  │                            │             │   "Genesys Cloud" icon
 │  └─ waits for commands ◀──────┼─────────────│◀─ icon click / session start
 │ Genesys Cloud desktop app     │             └──────────────┘
 └───────────────────────────────┘
```

All CPU and RAM for the web systems come from the session hosts. Each agent's share is capped (4 GB,
2 CPUs by default). The laptop only shows the session and runs Genesys.

## Files

| File | Goes to | Purpose |
|---|---|---|
| `CC-Shell.ps1` | `C:\Program Files\CallCenter\` | The shell that runs instead of the Windows desktop |
| `cc-shell.json` | `C:\Program Files\CallCenter\` | Broker URL, RDP address, domain, layout, path of the Genesys app |
| `Install-CCShell.ps1` | run once as admin | Copies the files; can switch one test user to the shell without Group Policy |

Keep `C:\Program Files\CallCenter` writable by administrators only. Whatever is in that folder runs at
every agent login.

## Settings to check in `cc-shell.json`

- `BrokerUrl`: `https://broker-01.<domain>:8443`. In production the broker must use HTTPS (set
  `tls_cert`/`tls_key` in its `broker.json`) with a certificate the laptops trust.
- `RdpHost`: the HAProxy virtual IP name of the session-host farm.
- `Layout`:
  - `docked` (recommended): the server desktop fills the screen except a strip on the right,
    `GenesysWidth` pixels wide, where the Genesys window is placed. Both stay visible, so there is no
    window juggling during calls.
  - `fullscreen`: the server desktop fills the screen and Genesys comes to the front when started
    or clicked.
- `Apps.genesys.Path` and `ProcessName`: **check these on one laptop.** Right-click the Genesys
  shortcut, choose *Open file location*, and use that exe path and its process name without `.exe`.
  The values given are a guess for a per-user install.
- `RdpAuthenticationLevel`: 2 needs a trusted certificate on the session hosts (xrdp `cert_path`).
  Use 0 only in the lab.

## Group Policy (Windows 10/11 Pro or Enterprise)

Create a GPO **"CC Agent Laptop"** linked to the OU of the call-center laptops.

Computer Configuration:
1. *System → Group Policy → Configure user Group Policy loopback processing mode* = **Merge**
   (user settings below then apply to whoever logs in to these laptops).
2. *Windows Settings → Security Settings → Restricted Groups* (or LAPS): agents are not local admins.
3. *Windows Components → BitLocker*: encrypt laptops that go home.
4. *System → Removable Storage Access → All Removable Storage classes: Deny all access* = Enabled.

User Configuration. Use security filtering on the GPO, or item-level targeting, so this applies to
**CC-Agents** only, and IT staff still get the normal desktop:

5. *Administrative Templates → System → Custom User Interface* = Enabled, interface file name:
   ```
   powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Program Files\CallCenter\CC-Shell.ps1"
   ```
   This replaces `explorer.exe`: no desktop, Start menu or taskbar.
6. *System → Ctrl+Alt+Del Options*: Remove Task Manager = Enabled, Remove Switch User = Enabled. Keep
   Lock and Change password, so password expiry still works the normal Windows way.
7. *System → Prevent access to the command prompt* = Enabled, and *Prevent access to registry
   editing tools* = Enabled.
8. *Windows Components → Remote Desktop Services → Remote Desktop Connection Client → Do not allow
   passwords to be saved* must stay **Not configured**. The shell stores the one-time ticket for a few
   seconds.

On Enterprise/Education, *Shell Launcher v2* is an alternative to step 5 with the same effect.

## Genesys Cloud desktop app

Install it on the laptops the way you do today (SCCM/Intune/MSI). It is not started at Windows login.
The server desktop starts it when the session opens, and its icon brings it back. Test G-1..G-9 and
H-1..H-4 with it.

## Home

Use your firewall's always-on (pre-logon) VPN on the laptops, so the domain controller, the broker and
the RDP farm are reachable before the agent logs in. With split tunnelling, Genesys media goes direct
to the internet.

## What has been tested

The broker, the session-host side and the protocol are tested in the simulated lab
(`tests/test_11_viewer.py`), with a Linux stand-in laptop running `services/cc-laptop-agent.py`. That
script does the same steps as `CC-Shell.ps1`. **`CC-Shell.ps1` itself has not been run on Windows
yet.** Follow `docs/MANUAL_TESTS.md` W-1..W-8 on one test laptop first.
