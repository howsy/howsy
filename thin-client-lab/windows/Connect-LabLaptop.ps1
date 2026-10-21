# Connects this Windows machine to the simulated lab running in a VM.
# Run in PowerShell as administrator, after "sudo ./lab.sh connect-laptop <this-ip>" on the VM.
#
#   .\Connect-LabLaptop.ps1 -VmIp 192.168.1.50              route + DNS + connectivity check
#   .\Connect-LabLaptop.ps1 -VmIp 192.168.1.50 -JoinDomain  then join LAB (restarts Windows)
#   .\Connect-LabLaptop.ps1 -Undo                           remove the route and restore DNS
#
# Joining the lab domain takes the machine OUT of any domain it is in now.
# Use a spare laptop or a Windows VM, never your everyday work laptop.
param([string]$VmIp, [switch]$JoinDomain, [switch]$Undo)
$ErrorActionPreference = 'Stop'

$LabNet = '10.10.0.0'; $LabMask = '255.255.255.0'; $Dc = '10.10.0.10'
$Domain = 'lab.example.com'
$OuPath = 'OU=Computers,OU=CallCenterTest,DC=lab,DC=example,DC=com'
$state = Join-Path $env:ProgramData 'CallCenterLab'
New-Item -ItemType Directory -Force -Path $state | Out-Null
$backup = Join-Path $state 'dns-backup.json'

if ($Undo) {
  & route.exe delete $LabNet | Out-Null
  if (Test-Path $backup) {
    $b = Get-Content -Raw $backup | ConvertFrom-Json
    if ($b.Servers) { Set-DnsClientServerAddress -InterfaceIndex $b.Index -ServerAddresses $b.Servers }
    else { Set-DnsClientServerAddress -InterfaceIndex $b.Index -ResetServerAddresses }
    Remove-Item $backup
  }
  Clear-DnsClientCache
  Write-Host "Lab route removed and DNS restored."
  return
}

if (-not $VmIp) { throw "Give the VM's IP address: -VmIp <address shown by 'sudo ./lab.sh connect-laptop'>" }

# 1. Route the lab's corporate network (10.10.0.0/24) through the VM.
$ifIndex = (Find-NetRoute -RemoteIPAddress $VmIp | Select-Object -First 1).InterfaceIndex
& route.exe delete $LabNet | Out-Null
& route.exe -p add $LabNet mask $LabMask $VmIp | Out-Null
Write-Host "Route: $LabNet/24 via $VmIp (interface $ifIndex)"

# 2. Use the lab's domain controller for DNS on that network adapter (old setting is saved).
if (-not (Test-Path $backup)) {
  $old = (Get-DnsClientServerAddress -InterfaceIndex $ifIndex -AddressFamily IPv4).ServerAddresses
  @{ Index = $ifIndex; Servers = $old } | ConvertTo-Json | Set-Content $backup
}
Set-DnsClientServerAddress -InterfaceIndex $ifIndex -ServerAddresses $Dc
Clear-DnsClientCache
Write-Host "DNS: $Dc (undo with -Undo)"

# 3. Check that everything the laptop needs answers.
$ok = $true
foreach ($name in "dc01.$Domain", "broker-01.$Domain", "lb.$Domain") {
  try { $ip = (Resolve-DnsName $name -Type A -DnsOnly -ErrorAction Stop | Select-Object -First 1).IPAddress
        Write-Host ("  DNS  {0,-28} {1}" -f $name, $ip) }
  catch { Write-Host "  DNS  $name  FAILED" -ForegroundColor Red; $ok = $false }
}
$checks = @(
  @{ Name = 'AD: DNS';             Host = $Dc;          Port = 53 },
  @{ Name = 'AD: Kerberos';        Host = $Dc;          Port = 88 },
  @{ Name = 'AD: RPC';             Host = $Dc;          Port = 135 },
  @{ Name = 'AD: LDAP';            Host = $Dc;          Port = 389 },
  @{ Name = 'AD: SMB';             Host = $Dc;          Port = 445 },
  @{ Name = 'AD: password change'; Host = $Dc;          Port = 464 },
  @{ Name = 'Broker';              Host = '10.10.0.25'; Port = 8088 },
  @{ Name = 'Management console';  Host = '10.10.0.25'; Port = 8090 },
  @{ Name = 'Desktop farm (RDP)';  Host = '10.10.0.30'; Port = 3389 }
)
foreach ($c in $checks) {
  $r = Test-NetConnection -ComputerName $c.Host -Port $c.Port -WarningAction SilentlyContinue
  if ($r.TcpTestSucceeded) { Write-Host ("  OK   {0,-22} {1}:{2}" -f $c.Name, $c.Host, $c.Port) -ForegroundColor Green }
  else { Write-Host ("  FAIL {0,-22} {1}:{2}" -f $c.Name, $c.Host, $c.Port) -ForegroundColor Red; $ok = $false }
}
if (-not $ok) {
  Write-Host "`nNot everything is reachable. On the VM: is the lab running (./lab.sh start), and was"
  Write-Host "'sudo ./lab.sh connect-laptop <this machine's IP>' run with THIS machine's address?"
  return
}
Write-Host "`nThis machine can reach the lab." -ForegroundColor Green
Write-Host "Kerberos needs matching clocks: keep 'Set time automatically' on here and on the VM."

# 4. Optionally join the lab domain.
if ($JoinDomain) {
  $edition = (Get-CimInstance Win32_OperatingSystem).Caption
  if ($edition -match 'Home') { throw "$edition cannot join a domain; use Pro, Enterprise or Education." }
  Write-Host "`nJoining $Domain. Enter LAB\Administrator and the ADMIN_PASSWORD from the lab's .env file."
  $cred = Get-Credential -UserName 'LAB\Administrator' -Message 'Lab domain administrator'
  Add-Computer -DomainName $Domain -OUPath $OuPath -Credential $cred -Force -Restart
}
