# Installs the call-center shell on one laptop (run as administrator).
#   .\Install-CCShell.ps1                       copy files only (Group Policy switches the shell)
#   .\Install-CCShell.ps1 -TestUser LAB\agent1  also switch this one user to the shell, for a test
#   .\Install-CCShell.ps1 -Remove -TestUser LAB\agent1   give the user the normal desktop back
param([string]$TestUser, [switch]$Remove)
$ErrorActionPreference = 'Stop'
$dest = Join-Path $env:ProgramFiles 'CallCenter'
$shell = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dest\CC-Shell.ps1`""

if (-not $Remove) {
  New-Item -ItemType Directory -Force -Path $dest | Out-Null
  Copy-Item -Force -Destination $dest -Path (Join-Path $PSScriptRoot 'CC-Shell.ps1'), (Join-Path $PSScriptRoot 'cc-shell.json')
  # Only administrators may change what runs at agent login.
  & icacls.exe $dest /inheritance:r /grant:r 'Administrators:(OI)(CI)F' 'SYSTEM:(OI)(CI)F' 'Users:(OI)(CI)RX' | Out-Null
  Write-Host "Installed to $dest"
}

if ($TestUser) {
  # Same registry value the "Custom User Interface" policy sets, for one user only.
  $sid = (New-Object System.Security.Principal.NTAccount($TestUser)).Translate([System.Security.Principal.SecurityIdentifier]).Value
  $hive = "Registry::HKEY_USERS\$sid"
  $loaded = Test-Path $hive
  if (-not $loaded) {
    $profile = (Get-CimInstance Win32_UserProfile | Where-Object SID -eq $sid).LocalPath
    if (-not $profile) { throw "$TestUser has not logged in to this laptop yet; log in once first." }
    & reg.exe load "HKU\$sid" "$profile\NTUSER.DAT" | Out-Null
  }
  try {
    $key = "$hive\Software\Microsoft\Windows\CurrentVersion\Policies\System"
    New-Item -Force -Path $key | Out-Null
    if ($Remove) {
      Remove-ItemProperty -Path $key -Name Shell -ErrorAction SilentlyContinue
      Write-Host "$TestUser gets the normal Windows desktop at next login"
    } else {
      Set-ItemProperty -Path $key -Name Shell -Value $shell
      Write-Host "$TestUser gets the call-center shell at next login"
    }
  } finally {
    if (-not $loaded) { [gc]::Collect(); & reg.exe unload "HKU\$sid" | Out-Null }
  }
}
