# Call-center laptop shell (replaces explorer.exe for agents via Group Policy).
#
# After the agent logs in to Windows with AD, this runs instead of the Windows
# desktop. It is the Windows version of services/cc-laptop-agent.py:
#   1. gets a one-time password from cc-broker with the agent's Kerberos ticket,
#   2. opens the server desktop in Remote Desktop (no second password),
#   3. waits for "open on my laptop" commands from that desktop and starts or
#      brings forward the local app (the Genesys Cloud desktop app),
#   4. reports to cc-console (health, events) and carries out the admins' actions:
#      view screen, message, start Genesys, reconnect, sign out,
#   5. when the remote desktop closes: reconnect, or sign the agent out.
# The agent never sees the laptop's own desktop, Start menu or taskbar.
#
# NOT YET RUN ON WINDOWS: the protocol is tested in the simulated lab with the
# Linux reference agent; run docs/MANUAL_TESTS.md W-1..W-8 on a test laptop.

$ErrorActionPreference = 'Stop'
$cfg = Get-Content -Raw (Join-Path $PSScriptRoot 'cc-shell.json') | ConvertFrom-Json
$logDir = Join-Path $env:LOCALAPPDATA 'CallCenter'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'cc-shell.log'
function Log([string]$msg) { Add-Content -Path $logFile -Value ("{0:s} {1}" -f (Get-Date), $msg) }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Security
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class Win {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int ht, uint flags);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
'@

$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$docked = ($cfg.Layout -eq 'docked')
# Docked: server desktop on the left, Genesys on the right, both always visible.
$desktopWidth = if ($docked) { $screen.Width - $cfg.GenesysWidth } else { $screen.Width }

function Get-Ticket {
  # Kerberos (Negotiate) with the logged-in agent's credentials.
  Invoke-RestMethod -Method Post -Uri "$($cfg.BrokerUrl)/sso/ticket" -UseDefaultCredentials -TimeoutSec 15
}

function Start-Desktop {
  $t = Get-Ticket
  $target = "TERMSRV/$($cfg.RdpHost)"
  # mstsc picks up saved TERMSRV credentials; the ticket is single-use and expires in minutes.
  & cmdkey.exe /generic:$target /user:"$($cfg.Domain)\$($t.username)" /pass:"$($t.password)" | Out-Null
  $rdp = Join-Path $logDir 'cc-desktop.rdp'
  # Also put the ticket in the .rdp file, encrypted for this Windows user (DPAPI):
  # xrdp has no NLA, and this is the way mstsc reliably logs in to it.
  $enc = [System.Security.Cryptography.ProtectedData]::Protect(
           [Text.Encoding]::Unicode.GetBytes($t.password), $null, 'CurrentUser')
  $lines = @(
    "full address:s:$($cfg.RdpHost)",
    "username:s:$($cfg.Domain)\$($t.username)",
    "password 51:b:$(($enc | ForEach-Object { $_.ToString('X2') }) -join '')",
    "prompt for credentials:i:0",
    "enablecredsspsupport:i:0",          # xrdp does not do NLA
    "authentication level:i:$($cfg.RdpAuthenticationLevel)",
    "displayconnectionbar:i:0",          # no bar to minimise back to the laptop
    "keyboardhook:i:1",                  # Windows key, Alt+Tab go to the server desktop
    "autoreconnection enabled:i:1",
    "audiomode:i:0",
    "redirectclipboard:i:0",
    "redirectprinters:i:0",
    "redirectdrives:i:0",
    "redirectsmartcards:i:0",
    "smart sizing:i:1"
  )
  if ($docked) {
    $lines += "screen mode id:i:1", "desktopwidth:i:$desktopWidth", "desktopheight:i:$($screen.Height)",
              "winposstr:s:0,1,0,0,$desktopWidth,$($screen.Height)"
  } else {
    $lines += "screen mode id:i:2"
  }
  Set-Content -Path $rdp -Value $lines -Encoding ASCII
  $p = Start-Process -FilePath mstsc.exe -ArgumentList "`"$rdp`"" -PassThru
  Log "desktop opened for $($t.username) (mstsc pid $($p.Id))"
  Start-Sleep -Seconds 20
  & cmdkey.exe /delete:$target | Out-Null     # never leave the ticket stored
  Remove-Item -Force $rdp -ErrorAction SilentlyContinue
  return $p
}

function Show-Window($handle, [bool]$dock) {
  if ($dock) { [Win]::SetWindowPos($handle, [IntPtr]::Zero, $desktopWidth, 0, $cfg.GenesysWidth, $screen.Height, 0x0040) | Out-Null }
  [Win]::ShowWindowAsync($handle, 9) | Out-Null          # SW_RESTORE
  [Win]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)         # tap Alt so Windows allows the focus change
  [Win]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
  [Win]::SetForegroundWindow($handle) | Out-Null
}

function Start-OrFocus([string]$id) {
  $app = $cfg.Apps.$id
  if (-not $app) { Log "ignored unknown app '$id'"; return }   # only apps listed in cc-shell.json
  $proc = Get-Process -Name $app.ProcessName -ErrorAction SilentlyContinue |
          Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
  if (-not $proc) {
    Log "starting $id"
    Start-Process -FilePath ([Environment]::ExpandEnvironmentVariables($app.Path))
    for ($i = 0; $i -lt 40 -and -not $proc; $i++) {
      Start-Sleep -Milliseconds 500
      $proc = Get-Process -Name $app.ProcessName -ErrorAction SilentlyContinue |
              Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
    }
  } else {
    Log "bringing $id to front"
  }
  if ($proc) { Show-Window $proc.MainWindowHandle $docked }
}

# ---- cc-console: report this laptop and carry out the admins' actions ---------
$script:events = New-Object System.Collections.ArrayList
$script:lastCheckin = [datetime]::MinValue
$script:reconnect = $false
$script:signOut = $false
$script:brokerOk = $true
$device = if ($cfg.DeviceName) { $cfg.DeviceName } else { $env:COMPUTERNAME.ToLower() }

function Add-Event([string]$kind, [string]$msg) {
  Log $msg
  [void]$script:events.Add(@{ ts = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); kind = $kind; message = $msg })
}

function Get-ScreenPng {
  Add-Type -AssemblyName System.Drawing
  $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
  $ms = New-Object System.IO.MemoryStream
  $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  [Convert]::ToBase64String($ms.ToArray())
}

function Invoke-ConsoleCommand($c) {
  $res = @{ id = $c.id; ok = $true; output = 'ok' }
  try {
    switch ($c.action) {
      'refresh'           { $script:lastCheckin = [datetime]::MinValue }
      'screenshot'        { $res.image = Get-ScreenPng; $res.output = 'laptop screen'
                            if ($c.args.first -eq '1') { Add-Event 'admin' 'an administrator viewed the laptop screen' } }
      'message'           { $text = $c.args.text -replace "'", "''"
                            Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command',
                              "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('$text','Message from IT')"
                            Add-Event 'message' "message from IT: $($c.args.text)"; $res.output = 'shown to the agent' }
      'start_app'         { Start-OrFocus $c.args.app; $res.output = "$($c.args.app) started or brought to front" }
      'reconnect_desktop' { $script:reconnect = $true; Get-Process mstsc -ErrorAction SilentlyContinue | Stop-Process -Force
                            $res.output = 'reconnecting' }
      'sign_out'          { $script:signOut = $true; Get-Process mstsc -ErrorAction SilentlyContinue | Stop-Process -Force
                            $res.output = 'signing out' }
      default             { $res.ok = $false; $res.output = "not supported on this laptop: $($c.action)" }
    }
  } catch { $res.ok = $false; $res.output = "$_" }
  try {
    Invoke-RestMethod -Method Post -Uri "$($cfg.ConsoleUrl)/agent/result" -UseDefaultCredentials -TimeoutSec 30 `
      -ContentType 'application/json' -Body ($res | ConvertTo-Json -Compress) | Out-Null
  } catch { Log "console result: $_" }
}

function Sync-Console {
  if (-not $cfg.ConsoleUrl -or ((Get-Date) - $script:lastCheckin).TotalSeconds -lt 15) { return }
  $script:lastCheckin = Get-Date
  $genesys = $cfg.Apps.genesys
  $os = (Get-CimInstance Win32_OperatingSystem)
  $body = @{
    device = $device; role = 'laptop'; events = @($script:events)
    facts = @{
      os = "$($os.Caption) $($os.Version)"; shell = 'CC-Shell.ps1'
      desktop_open = [bool](Get-Process mstsc -ErrorAction SilentlyContinue)
      genesys_running = [bool]($genesys -and (Get-Process -Name $genesys.ProcessName -ErrorAction SilentlyContinue))
      broker_reachable = $script:brokerOk
      metrics = @{ mem_pct = [math]::Round(100 * (1 - $os.FreePhysicalMemory / $os.TotalVisibleMemorySize), 1) }
    }
    compliance = @{
      call_center_shell = @{ ok = $true; detail = 'laptop desktop replaced'; severity = 'fail' }
      broker_reachable  = @{ ok = $script:brokerOk; detail = 'single sign-on broker'; severity = 'fail' }
      genesys_installed = @{ ok = [bool]($genesys -and (Test-Path ([Environment]::ExpandEnvironmentVariables($genesys.Path))));
                             detail = 'Genesys desktop app installed'; severity = 'warn' }
    }
  }
  try {
    $reply = Invoke-RestMethod -Method Post -Uri "$($cfg.ConsoleUrl)/agent/checkin" -UseDefaultCredentials -TimeoutSec 20 `
               -ContentType 'application/json' -Body ($body | ConvertTo-Json -Depth 6 -Compress)
    $script:events.Clear()
    foreach ($c in $reply.commands) { Invoke-ConsoleCommand $c }
  } catch { Log "console: $_" }
}

while ($true) {
  try {
    $mstsc = Start-Desktop
    Add-Event 'desktop' "remote desktop opened for $env:USERNAME"
  } catch {
    Log "cannot open desktop: $_"
    $r = [System.Windows.Forms.MessageBox]::Show("The call-center desktop is not reachable.`n`nTry again?",
           'Call Center', 'RetryCancel', 'Warning')
    if ($r -eq 'Retry') { continue } else { break }
  }
  while (-not $mstsc.HasExited) {
    Sync-Console
    try {
      $msg = Invoke-RestMethod -Uri "$($cfg.BrokerUrl)/launch/wait?timeout=10" -UseDefaultCredentials -TimeoutSec 25
      $script:brokerOk = $true
      foreach ($id in $msg.apps) { Start-OrFocus $id; Add-Event 'app' "open-on-laptop: $id" }
    } catch {
      $script:brokerOk = $false
      Log "broker: $_"
      Start-Sleep -Seconds 5
    }
  }
  Add-Event 'desktop' 'remote desktop closed'
  if ($script:signOut) { break }
  if ($script:reconnect) { $script:reconnect = $false; continue }
  $r = [System.Windows.Forms.MessageBox]::Show("Your desktop session has ended.`n`nReconnect?",
         'Call Center', 'YesNo', 'Question')
  if ($r -ne 'Yes') { break }
}
Add-Event 'desktop' 'signing out'
$script:lastCheckin = [datetime]::MinValue
Sync-Console
& shutdown.exe /l
