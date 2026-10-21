# Run the lab in a VMware virtual machine

This builds one Ubuntu VM in VMware that holds the whole simulated lab. You watch everything on the
VM's screen: the machines starting, Ansible configuring them, the tests running, and then the portal,
the agent desktop and the Genesys launch.

Time: about 30 minutes of setup, plus 20–30 minutes for the first full run.

## 1. What you need

- VMware Workstation Pro or Player (Windows/Linux), or VMware Fusion (Mac).
- The Ubuntu **24.04 LTS Desktop** ISO from https://ubuntu.com/download/desktop
- Internet access for the VM.
- A GitHub account with access to `howsy/howsy`.
- A phone with an authenticator app (Google or Microsoft Authenticator), to try the portal's MFA.

## 2. Create the VM

1. VMware → **Create a New Virtual Machine** → *Typical* → **Installer disc image file (iso)** →
   pick the Ubuntu 24.04 ISO.
2. Name it `cc-lab`. Disk: **80 GB**, "store as a single file".
3. Before finishing, click **Customize Hardware**:
   - Memory: **16 GB** (12 GB minimum).
   - Processors: **4 cores** or more.
   - Network adapter: **NAT**.
   - Display: turn on 3D acceleration if offered (smoother desktop).
   - Processors → *Virtualization engine*: leave **all boxes unticked** (VT-x/AMD-V, CPU
     performance counters, IOMMU). The lab doesn't need them, and they often stop the VM from
     starting.
4. Finish and power on the VM.

## 3. Install Ubuntu

1. Choose **Install Ubuntu** → language and keyboard → **Normal installation**. Tick
   *Download updates* and *Install third-party software*.
2. **Erase disk and install Ubuntu**. This only affects the VM's virtual disk.
3. Create your user (e.g. `labadmin`) and a password.
4. Restart when asked (press Enter if it says "remove the installation medium") and log in.
5. Open **Terminal** (Ctrl+Alt+T) and update:
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y open-vm-tools-desktop git gh remmina remmina-plugin-rdp
   sudo reboot
   ```
   `open-vm-tools-desktop` gives you copy/paste between your computer and the VM, and resizing.

## 4. Install Docker

In the VM's terminal:
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```
If the script says your Ubuntu release is not supported (e.g. a very new release such as 26.04),
use Ubuntu's own Docker packages instead:
```bash
sudo apt install -y docker.io docker-compose-v2
sudo usermod -aG docker $USER
```
**Log out and log back in** (or reboot) so your user can use Docker. Then check:
```bash
docker run --rm hello-world        # prints "Hello from Docker!"
docker compose version             # prints v2.x
```

## 5. Get the code

The repository is private, so sign in to GitHub first:
```bash
gh auth login          # GitHub.com → HTTPS → Login with a web browser, follow the code shown
gh repo clone howsy/howsy -- -b claude/compassionate-einstein-6vzr6z
cd howsy/thin-client-lab
```

## 6. Build and test everything

```bash
./lab.sh all
```
What you will see, in order:

| Step | Takes | What happens |
|---|---|---|
| Building images | 5–10 min | Docker downloads Ubuntu, Guacamole, PostgreSQL, HAProxy, and builds the lab images. |
| Starting the lab | ~1 min | About 16 containers start: AD domain controller, broker, two session hosts, load balancer, portal, VPN, laptops… |
| Provisioning | 5–10 min | Ansible joins every machine to AD and installs xrdp, XFCE, the agent desktop, the broker and the VPN. Every line is one task. `failed=0` in the final **PLAY RECAP** means success. |
| Tests | ~5 min | Each dot is a passed test. It ends with something like `98 passed, 5 skipped`. The skipped ones need real hardware. |

At the end it prints the addresses and logins below.

**Take a VMware snapshot now** (VM → Snapshot → Take Snapshot, name it "lab ready"). If you break
something later, revert to it.

## 7. Look around like an agent

All of this is inside the VM.

**Remote portal (browser access, with MFA)**
1. Open Firefox at **https://localhost:8443/**. Click *Advanced → Accept the risk* (the lab uses a
   self-signed certificate).
2. Log in as `agent1` / `Agent-Pass1!`.
3. A QR code appears. Scan it with your authenticator app and type the 6-digit code.
4. Click **CC Desktop** (the name, not the `+`). The agent desktop opens in the browser.
5. Log out, then try `outsider1` / `Agent-Pass1!`. It's a real AD account outside the agents group,
   and it gets "Invalid login".

**The agent desktop, the way a laptop shows it**
1. Open **Remmina** → new connection → protocol **RDP**, server **localhost:13389**, user `agent1`,
   password `Agent-Pass1!` → Connect (accept the certificate).
2. You see the call-center desktop: an icon per system (CRM, Knowledge Base, Ticketing) and
   **Genesys Cloud**. The only bar has open windows, clock and log out. Double-click CRM: Chrome opens
   with every system as a tab.

**Genesys opening on the laptop, live**
1. Open a second terminal in `howsy/thin-client-lab` and run:
   ```bash
   ./lab.sh watch-laptop
   ```
   This terminal now plays agent1's Windows laptop. It shows `listening for agent1's laptop commands`.
2. In the Remmina desktop, double-click **Genesys Cloud**.
3. The terminal prints `starting genesys`, and on the next click `genesys already running: bring to
   front`. On a real Windows laptop, that's when the Genesys desktop app opens or comes to the front.
4. Ctrl+C stops it.

**Load balancer**: open **http://localhost:8404/** to see which session host each desktop went to.

## 8. Everyday commands

Run these in `howsy/thin-client-lab`:

| Command | Does |
|---|---|
| `./lab.sh stop` | Pause the lab and keep everything configured (do this before shutting the VM down). |
| `./lab.sh start` | Resume it (wait about a minute before logging in). |
| `./lab.sh test` | Run all tests again. |
| `./lab.sh test tests/test_11_viewer.py` | Only the Windows-laptop / Genesys tests. |
| `./lab.sh test-fast` | Skip the slow tests (failover, fail2ban ban, playbook re-run). |
| `./lab.sh golden-image` | Throw away a laptop, rebuild it from bare Ubuntu, re-test. |
| `./lab.sh info` | Show addresses and logins. |
| `./lab.sh shell` | A shell with Ansible and the tests, inside the lab. |
| `./lab.sh clean` then `./lab.sh all` | Start over from nothing. |

`./lab.sh down` removes the lab machines. After it, run `./lab.sh up` and then `./lab.sh provision`.
Use `stop`/`start` instead to pause.

## 9. If something goes wrong

| Problem | Fix |
|---|---|
| Boot shows *"piix4_smbus … SMBus Host Controller not enabled!"* | Harmless VMware message. Ignore it. With *Easy Install*, wait until Ubuntu restarts at its login screen. |
| VMware: *"does not support virtualized performance counters"*, *"Module 'VPMC' power on failed"* | Power the VM off → Edit settings → Processors → untick **Virtualize CPU performance counters** (and the other virtualization boxes). If it is greyed out: close VMware, add or change `vpmc.enable = "FALSE"` in the VM's `.vmx` file. |
| VMware: errors about VT-x, Hyper-V or Credential Guard | Untick the virtualization boxes as above and update VMware Workstation to 17.5 or newer. |
| `permission denied ... docker.sock` | You did not log out/in after step 4. Log out and back in. |
| Very slow, or containers get killed | Give the VM more RAM (16 GB) and CPUs. Check with `free -h`. |
| A provisioning task failed | Run `./lab.sh provision` again. Most steps can be repeated safely. The error is just above **PLAY RECAP**. |
| Portal shows "An error has occurred" | Wait a minute after `start`, then reload. Check `docker compose ps` (all should be *Up*). |
| A test failed | Run that file again: `./lab.sh test tests/<file> -x`. The output names the check that failed. |
| Logs of one machine | `docker compose logs <name>`, e.g. `docker compose logs guac`. For the managed machines: `docker exec -it cclab-sh-01 journalctl -u xrdp`. |
| Chrome not installed on the session hosts | The VM could not reach `dl.google.com`. Fix the internet access and run `./lab.sh provision`. |

## 10. Connect a real Windows laptop to the lab

This makes a Windows machine a member of the lab's AD, so you can log in as `LAB\agent1` and see the
real thing: no Windows desktop, the server desktop opening with no second password, and a local app
opening on the laptop from the server desktop's **Genesys Cloud** icon. Until Genesys is installed,
Notepad stands in for it.

### 10.1 Pick the Windows machine

| Choice | When | VMware network of the Ubuntu VM |
|---|---|---|
| **A Windows VM next to the Ubuntu VM** (recommended) | Nothing real is touched; you can snapshot it. Use the free 90-day *Windows 11 Enterprise evaluation* ISO from Microsoft's Evaluation Center. 4 GB RAM, 2 CPUs, 64 GB disk. | NAT (unchanged) |
| A spare physical laptop | You want real hardware, headsets, docked layout on its screen. | **Bridged** (VM → Settings → Network Adapter → Bridged, then reboot the Ubuntu VM) |
| The computer that runs VMware | Only if it is **not** your work laptop. Joining the lab domain removes it from your company domain. | NAT |

The Windows machine must be **Pro, Enterprise or Education**, because Home editions can't join a domain.

### 10.2 Find the two IP addresses

- **Ubuntu VM:** run `ip -4 addr show` and take the address on `ens33` (e.g. `192.168.142.128`).
- **Windows machine:** run `ipconfig` and take its IPv4 address. If the Windows machine is the VMware
  host itself, use the address of the adapter *VMware Network Adapter VMnet8*.

### 10.3 On the Ubuntu VM: let that machine in

```bash
cd ~/howsy/thin-client-lab
./lab.sh start                       # if it was stopped
./lab.sh connect-laptop 192.168.142.130      # the Windows machine's IP; asks for your sudo password
```
This restarts Docker once; the lab comes back by itself within a minute. Only that one address is
let in, and it stays allowed after reboots.

### 10.4 On Windows: connect and join the lab domain

1. Get the `windows` folder of the repo onto the machine. Either use GitHub in a browser
   (`howsy/howsy`, branch `claude/compassionate-einstein-6vzr6z`, *Code → Download ZIP*, then unzip),
   or use Git: `git clone -b claude/compassionate-einstein-6vzr6z https://github.com/howsy/howsy.git`.
2. Open **PowerShell as administrator** in `thin-client-lab\windows` and run:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\Connect-LabLaptop.ps1 -VmIp 192.168.142.128      # the Ubuntu VM's IP
   ```
   Every line should say **OK** and end with *This machine can reach the lab.*
3. Join the domain:
   ```powershell
   .\Connect-LabLaptop.ps1 -VmIp 192.168.142.128 -JoinDomain
   ```
   Log in as `LAB\Administrator` with password `Lab-Admin-Pass1!` (from `.env`). Windows restarts.

### 10.5 Turn on the call-center shell for agent1

1. At the Windows login screen choose **Other user** and log in as `LAB\agent1` / `Agent-Pass1!`.
   This first login gives the normal Windows desktop and creates agent1's profile. Sign out.
2. Log in as `LAB\Administrator`, open PowerShell as administrator in `thin-client-lab\windows`:
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\Install-CCShell.ps1 -Config cc-shell.lab.json -TestUser LAB\agent1
   ```
   Sign out.

### 10.6 Log in as the agent and watch

Log in as `LAB\agent1` / `Agent-Pass1!`. What should happen:

1. No Windows desktop, Start menu or taskbar: a dark screen for a few seconds.
2. The call-center desktop opens **by itself with no second password**, on the left part of the
   screen (docked layout).
3. A few seconds later **Notepad opens on the right**: that's the "Genesys" app started on the laptop
   by the server desktop.
4. Minimise or click away from Notepad, then double-click **Genesys Cloud** on the server desktop:
   Notepad comes back to the front.
5. Log out from the server desktop (bottom-right button). The laptop asks **Reconnect?** *Yes* opens
   a fresh desktop, still with no password. *No* signs out of Windows.

Then go through tests **W-1 … W-9** in [`MANUAL_TESTS.md`](MANUAL_TESTS.md). On the Ubuntu VM,
`./lab.sh watch-laptop` is not needed here, because the Windows machine is the laptop now.

To use the real Genesys desktop app, install it on the Windows machine. Then edit
`C:\Program Files\CallCenter\cc-shell.json` as administrator and set `Apps.genesys.Path` and
`ProcessName` to the Genesys exe (see [`windows/README.md`](../windows/README.md)).

### 10.7 If something goes wrong

| Problem | Fix |
|---|---|
| `Connect-LabLaptop.ps1` shows FAIL lines | On the VM: `./lab.sh start`, wait a minute; check you gave `connect-laptop` **this** machine's IP (`ipconfig`). With Bridged networking the VM's IP may have changed: run both commands again. |
| Lab names don't resolve on Windows | Disable IPv6 on the network adapter (Windows may ask an IPv6 DNS server first), then `ipconfig /flushdns`. |
| Domain join says the domain can't be contacted | Same as above; also check the clocks of Windows and the VM agree (within 5 minutes). |
| Black screen after agent login, nothing opens | Press Ctrl+Alt+Del → Sign out. Log in as `LAB\Administrator` and read `C:\Users\agent1\AppData\Local\CallCenter\cc-shell.log`. "401" means a Kerberos problem (clock, DNS); "403" means the user is not in `CC-Agents-Test`. |
| A password prompt appears in the remote desktop | The one-time login was not accepted. Look at the same log, and on the VM: `docker exec cclab-broker-01 journalctl -u cc-broker -n 30`. |

### 10.8 Undo everything

On Windows, as administrator:
```powershell
.\Install-CCShell.ps1 -Remove -TestUser LAB\agent1      # agent1 gets the normal desktop back
Remove-Computer -UnjoinDomainCredential LAB\Administrator -WorkgroupName WORKGROUP -Restart
.\Connect-LabLaptop.ps1 -Undo                            # after the restart: route and DNS back
```
(Know a local administrator password before leaving the domain.) On the Ubuntu VM:
```bash
./lab.sh disconnect-laptop
```
