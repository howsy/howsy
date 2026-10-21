# Simulated call-center lab for Windows (Docker Desktop).
#   powershell -ExecutionPolicy Bypass -File .\lab.ps1 all
#   .\lab.ps1 up | provision | test | test-fast | golden-image | info | shell | stop | start | down | clean
#   .\lab.ps1 watch-laptop [user]   live view of what the agent's laptop is told to do
param([string]$Command = "help", [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Run([string]$exe, [string[]]$argv) {
  & $exe @argv
  if ($LASTEXITCODE -ne 0) { throw "failed: $exe $($argv -join ' ')" }
}
function Ctl([string[]]$argv) { Run "docker" (@("compose", "exec", "-T", "controller") + $argv) }
$extra = ($Rest -join " ")

function Up {
  if (-not (Test-Path .env)) { Copy-Item .env.example .env }
  # Windows PowerShell 5.1 turns redirected native stderr into errors under "Stop".
  $ErrorActionPreference = "Continue"
  docker image inspect cclab/managed-node *> $null
  $missing = ($LASTEXITCODE -ne 0)
  $ErrorActionPreference = "Stop"
  if ($missing) { Run "docker" @("build", "-t", "cclab/managed-node", "images/managed-node") }
  Run "docker" @("compose", "up", "-d", "--build", "controller")
  Ctl @("make", "prereqs")
  Run "docker" @("compose", "up", "-d", "--build", "--wait", "--wait-timeout", "600")
}

function Info {
  Write-Host @"

Lab is running. Try it like an agent would:

  Remote portal      https://localhost:8443/
                     user agent1 / Agent-Pass1!   (scan the QR code with any authenticator app)
                     user outsider1 is a real AD account that must be refused
  Session desktop    Remote Desktop (mstsc) to localhost:13389  (agent1 / Agent-Pass1!)
                     run ".\lab.ps1 watch-laptop" in another window, then double-click
                     "Genesys Cloud" on that desktop: the laptop starts / focuses Genesys
  HAProxy stats      http://localhost:8404/
  Management console http://localhost:8090/   (itadmin1 / It-Admin-Pass1!)

Passwords come from .env. The browser will warn about the self-signed certificate.
"@
}

switch ($Command) {
  "up"           { Up }
  "provision"    { Ctl @("make", "provision", "ANSIBLE_ARGS=$extra") }
  "test"         { Ctl @("make", "-s", "ssh-key"); Ctl (@("python3", "-m", "pytest") + $Rest) }
  "test-fast"    { Ctl @("make", "-s", "ssh-key"); Ctl (@("python3", "-m", "pytest", "-m", "not slow") + $Rest) }
  "golden-image" { Run "docker" @("compose", "rm", "-sf", "ep-office-01"); Run "docker" @("compose", "up", "-d", "--wait", "ep-office-01"); Ctl @("make", "golden-provision") }
  "all"          { Up; Ctl @("make", "provision"); Ctl @("make", "test"); Info }
  "info"         { Info }
  "watch-laptop" {
    $user = if ($Rest) { $Rest[0] } else { "agent1" }
    $pw = ((Get-Content .env | Where-Object { $_ -like "AGENT_PASSWORD=*" }) -replace '^AGENT_PASSWORD=', '')
    docker compose exec ep-office-01 su - $user -c "printf '%s' '$pw' | kinit $user >/dev/null && cc-laptop-listen"
  }
  "shell"        { docker compose exec controller bash }
  "stop"         { docker compose stop }
  "start"        { docker compose start; Info }
  "down"         { docker compose down }
  "clean"        { $ErrorActionPreference = "Continue"; docker compose exec -T controller rm -rf /lab/.lab; docker compose down -v --remove-orphans }
  default        { Get-Content $PSCommandPath -TotalCount 4 | Select-Object -Skip 1 }
}
