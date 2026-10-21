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
| `permission denied ... docker.sock` | You did not log out/in after step 4. Log out and back in. |
| Very slow, or containers get killed | Give the VM more RAM (16 GB) and CPUs. Check with `free -h`. |
| A provisioning task failed | Run `./lab.sh provision` again. Most steps can be repeated safely. The error is just above **PLAY RECAP**. |
| Portal shows "An error has occurred" | Wait a minute after `start`, then reload. Check `docker compose ps` (all should be *Up*). |
| A test failed | Run that file again: `./lab.sh test tests/<file> -x`. The output names the check that failed. |
| Logs of one machine | `docker compose logs <name>`, e.g. `docker compose logs guac`. For the managed machines: `docker exec -it cclab-sh-01 journalctl -u xrdp`. |
| Chrome not installed on the session hosts | The VM could not reach `dl.google.com`. Fix the internet access and run `./lab.sh provision`. |

## 10. Next: a real Windows laptop

The simulation plays the laptop with Linux. To try the real thing:
1. Use a test laptop with your normal Windows image, joined to AD.
2. Follow [`windows/README.md`](../windows/README.md): edit `cc-shell.json`, run
   `Install-CCShell.ps1 -TestUser DOMAIN\testagent`.
3. Work through tests W-1 to W-9 in [`MANUAL_TESTS.md`](MANUAL_TESTS.md).

A Windows laptop can't reach the lab inside this VM as-is. The lab's AD and broker live on Docker
networks inside the VM. Connecting them needs the real lab (Proxmox, `ansible/inventories/lab`) or
extra network setup. Ask before trying it.
