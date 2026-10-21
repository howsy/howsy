# Manual tests

These checklist items need a person, a headset, a real Genesys org or real
hardware. Run them in the real lab after the automated suite passes
(`LAB_TARGETS=tests/targets/lab.yml python3 -m pytest tests`). Record every run in the
results sheet at the end. A test passes only when every "Pass if" line holds.

Prerequisites for every session:
- the device has been built from the current golden image (`lab.sh golden-image`
  in the sim, PXE + Ansible in the real lab), and the automated endpoint tests
  pass on it
- test AD users in `CC-Agents-Test`, Genesys Cloud test users with WebRTC phones,
  a second phone or softphone that can call the test queue and take transfers

---

## Genesys Cloud (checklist: "login, inbound call, outbound call, transfer, wrap-up")

| ID | Where | Steps | Pass if |
|----|-------|-------|---------|
| G-1 | office endpoint | Power on, log in with the AD test user. | The Genesys window opens without any click, with no tabs or address bar. SSO or Genesys login works. |
| G-2 | office endpoint | Close the Genesys window (X, Alt+F4). | It comes back within ~5 s. |
| G-3 | office endpoint | Go On Queue. Call the test queue from the second phone and answer. Talk for 60 s each way. | Ringing is heard in the headset and the alert pops up. Both sides hear each other clearly, with no echo, choppiness or one-way audio. |
| G-4 | office endpoint | Place an outbound call to the second phone from the dialpad. | Connects. Audio clear both ways. |
| G-5 | office endpoint | During a call: blind transfer to a second agent or queue. Repeat with a consult transfer. | Both transfers complete. The consult leg has clear audio. |
| G-6 | office endpoint | End a call and pick a wrap-up code. | Wrap-up saved. The interaction shows the code in Genesys. |
| G-7 | office endpoint | Mute and hold during a call; use the headset's own mute and answer buttons. | Software mute and hold work. Note whether the headset buttons work: in a browser they depend on Genesys' WebHID headset integration for that vendor. |
| G-8 | home endpoint (VPN up) | Repeat G-3 to G-6 from a home connection. Then open Genesys' WebRTC diagnostics (Genesys Cloud → Help/Performance → WebRTC Diagnostics) and run the test. | Same results as the office. The diagnostics pass (STUN/TURN reachable, no media errors). |
| G-9 | office + home | After G-3..G-8, run `tools/genesys_call_quality.py` for the test users over the test window. | Median MOS ≥ 4.0 and no call below 3.5, or the same as the laptop baseline. |

## Headsets (checklist: "Headset audio clear on test calls, from the office and from home")

Repeat for **every headset model** in use today.

| ID | Steps | Pass if |
|----|-------|---------|
| H-1 | Plug the headset into an office endpoint. In Genesys → Settings → Audio, pick it for microphone, speaker and ringer. Make a test call (G-3). | Selected automatically or with one click. Clear audio. Volume and mute behave. |
| H-2 | Unplug and replug the headset in the middle of a call. | Audio returns without restarting Genesys (re-select it if needed, and write that down). |
| H-3 | Same model on the home endpoint (G-8). | Same as H-1. |
| H-4 | Plug in the headset and a USB stick at the same time. | Headset works. The stick is not mounted (see U-1). |

## Genesys Engage (checklist: "Engage agent workflow works")

| ID | Steps | Pass if |
|----|-------|---------|
| E-1 | If Workspace Web Edition is deployed: open it in Chrome on the office endpoint, log in, go ready, take and release a call. | Works end to end. Note the URL so it can go in the app inventory and the Chrome policies. |
| E-2 | Same from the home endpoint (Engage is on-premises, so traffic goes through the VPN). | Works. `ip route get <engage server IP>` on the endpoint shows `dev wg0` (or the firewall VPN interface). |
| E-3 | Desk IP phone path: log the Engage test agent into a desk phone and take a call. | Works. Agent state is consistent between the phone and Engage. |

## Other apps (checklist: "Every other call-center app opens and works")

| ID | Steps | Pass if |
|----|-------|---------|
| A-1 | For **each** row in `tests/apps.lab.yml`, from each place it is used (endpoint browser or the session-host desktop): log in, search a record, open it, save a change, print or export if agents do that today. | Every app works, or is listed with a workaround. Any Windows-only app goes on the risks list (small Windows RDS server). |

The automated `test_04_apps.py` only proves each app is reachable from each place.

## Login and passwords

| ID | Steps | Pass if |
|----|-------|---------|
| P-1 | On the endpoint login screen, log in as a user whose password has expired ("must change at next logon"). | The login screen asks for a new password, accepts it, and the desktop opens. The new password works on the session hosts and the portal. |
| P-2 | Log in as a user who is **not** in `CC-Agents-Test` on the endpoint login screen, through RDP and on the portal. | Refused in all three places. |
| P-3 | Decide and test how agents change their password **before** it expires (for example `passwd` from a terminal, or the Windows tools they use today). | A documented, working path exists. |
| P-4 | Home endpoint, office network unplugged: a user who has logged in on that device before logs in. Then a user who has never logged in on it. | Both work while the VPN is up. With the VPN down, only the cached user can log in. |

## Endpoint hardware and lockdown

| ID | Steps | Pass if |
|----|-------|---------|
| U-1 | Plug in a USB stick, a USB hard disk and a phone (MTP). | No storage appears in Files or `lsblk`. Write down whether the phone exposes files: MTP is not blocked by the usb-storage rule, so consider USBGuard. |
| U-2 | As the agent: try `sudo -i`, open Settings → Users or Software, try installing an app. | All refused or asking for an admin password the agent does not have. |
| U-3 | Leave the endpoint idle for longer than the idle timeout. | Screen locks and needs the AD password. |
| U-4 | Open `chrome://policy` in Chrome. | The policies from `cc-policy.json` show as "Platform / Mandatory". Downloads, DevTools and incognito are blocked. |
| D-1 | Reboot a home endpoint. | The disk is encrypted. Write down how it unlocks (passphrase, TPM, network unlock). An agent should not need a separate disk passphrase unless that is the decision. |

## VPN on real hardware (checklist: "VPN connects before login")

| ID | Steps | Pass if |
|----|-------|---------|
| V-1 | Take the home endpoint to a home or 4G network. Power on. Wait at the login screen for 30 s, then log in. | `wg show` (or the firewall client) shows a recent handshake before the first login. AD login works. An internal app opens. |
| V-2 | During a Genesys call from home, drop Wi-Fi for 10 s. | The tunnel comes back on its own. Note whether the call survives: Genesys media is direct, not in the tunnel. |
| V-3 | If the firewall VPN with MFA is used: check how MFA fits with "connect before login". | Documented. An interactive MFA prompt cannot appear before anyone has logged in, so pre-login tunnels normally use a device certificate. |

## Golden image on new hardware (checklist: "Golden image deploys to a new device without manual steps")

| ID | Steps | Pass if |
|----|-------|---------|
| I-1 | PXE-boot a brand-new mini PC and a reused laptop. Do not touch the keyboard after choosing PXE. Then run `ansible-playbook site.yml --limit <device>` and `LAB_TARGETS=... python3 -m pytest tests -m endpoint`. | The device installs, joins AD and passes every endpoint test with no manual step. Note the minutes taken. |

## Pilot measures

| ID | Steps | Pass if |
|----|-------|---------|
| L-1 | Stopwatch from pressing the power button to the agent being On Queue in Genesys. 5 runs per device type, office and home. | Each run under 2 minutes. |
| L-2 | Twice per shift during the pilot, run `tools/sessionhost_usage.py`. | Gives MB and CPU per user, which size production. Compare with the rule of thumb of 2 GB and 0.5 vCPU per user. |
| L-3 | Weekly during the pilot, run `tools/genesys_call_quality.py` with pilot agents and a laptop baseline group. Count call-quality tickets for both groups. | Pilot no worse than baseline. |

---

## Results sheet

Copy this table for each test session.

| Date | Tester | Device / model | Location | Test ID | Pass/Fail | Notes (headset model, network, ticket #) |
|------|--------|----------------|----------|---------|-----------|------------------------------------------|
|      |        |                |          |         |           |                                          |
