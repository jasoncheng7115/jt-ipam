<#
.SYNOPSIS
  Install / update / remove the jt-ipam certificate distribution agent on Windows.

.DESCRIPTION
  Downloads the agent from your jt-ipam server, writes a config file, and registers a daily
  scheduled task that runs it as SYSTEM. Windows PowerShell 5.1 (built into Windows Server
  2019 and later) is all that is required -- no extra modules.

  Run the install command shown for the agent in jt-ipam (Certificates > Agents) from an
  elevated PowerShell prompt. It sets these variables and then loads this script:

    $env:JT_IPAM_SERVER = "https://ipam.example.com"
    $env:JT_IPAM_AGENT_KEY = "<enrollment key from the jt-ipam cert agent page>"
    $env:JT_IPAM_CERT_SHA256 = "<SHA-256 of the jt-ipam server certificate>"   # the install command fills this in
    # ($env:JT_IPAM_INSECURE = "1" skips server verification entirely -- not recommended)

  (The one-line download-and-run example is not written out here on purpose: YARA rules used by
  EDR products flag that combination even inside a comment.)

  Or, having saved this file:

    .\jt-ipam-cert-agent-installer.ps1 -Server https://ipam.example.com -AgentKey <key>

.PARAMETER Uninstall
  Remove the scheduled task and the installed files. Certificates already imported into the
  Windows certificate store are left alone -- they are in use.
#>

param(
    [string] $Server,
    [string] $AgentKey,
    [string] $Time = "03:30",
    [switch] $NoVerifyTls,
    [switch] $Uninstall
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$INSTALL_DIR = "C:\Program Files\jt-ipam-cert-agent"
$DATA_DIR = "C:\ProgramData\jt-ipam-cert-agent"
$AGENT_PATH = Join-Path $INSTALL_DIR "jt_ipam_cert_agent.ps1"
$CONFIG_PATH = Join-Path $DATA_DIR "config"
$LOG_PATH = Join-Path $DATA_DIR "last-run.log"
$TASK_NAME = "jt-ipam-cert-agent"

function Write-Step { param([string] $Message) Write-Host "==> $Message" -ForegroundColor Cyan }

function Invoke-Native {
    <# $ErrorActionPreference = "Stop" plus `2>&1` on a native command makes anything the
       command writes to stderr a *terminating* error in Windows PowerShell 5.1 -- so
       deleting a scheduled task that does not exist yet would abort the install. #>
    param([string] $File, [string[]] $Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $File @Arguments 2>&1
        $rc = $LASTEXITCODE          # capture before anything else can touch it
        $text = ($out | Out-String).Trim()
        $lines = @($text -split "`r?`n" | Where-Object { $_.Trim() -and $_ -notmatch "^\s*\+|^\s*at |CategoryInfo|FullyQualifiedErrorId" })
        $brief = ($lines | Select-Object -First 3) -join " / "
        return [pscustomobject]@{ Output = $text; Brief = $brief; ExitCode = $rc }
    } finally { $ErrorActionPreference = $prev }
}

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "This installer must be run from an elevated PowerShell (Run as Administrator)."
    }
}

function Remove-Install {
    Write-Step "Removing scheduled task and files"
    Unregister-ScheduledTask -TaskName $TASK_NAME -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $INSTALL_DIR) { Remove-Item -LiteralPath $INSTALL_DIR -Recurse -Force }
    # Config holds the agent key; state and log are ours too.
    if (Test-Path -LiteralPath $DATA_DIR) { Remove-Item -LiteralPath $DATA_DIR -Recurse -Force }
    Write-Host "Removed. Certificates already imported into the Windows certificate store were left in place."
}

Assert-Admin

# The one-liner form pipes this script into `iex`, which cannot pass -switches -- so the
# same choices are also readable from the environment.
if ($env:JT_IPAM_UNINSTALL) { $Uninstall = $true }
if ($env:JT_IPAM_INSECURE) { $NoVerifyTls = $true }
$CertSha256 = "$env:JT_IPAM_CERT_SHA256".Trim()
if ($env:JT_IPAM_TIME) { $Time = $env:JT_IPAM_TIME }

if ($Uninstall) { Remove-Install; exit 0 }

if (-not $Server) { $Server = $env:JT_IPAM_SERVER }
if (-not $AgentKey) { $AgentKey = $env:JT_IPAM_AGENT_KEY }
if (-not $Server) { throw "Server is required (-Server https://ipam.example.com or `$env:JT_IPAM_SERVER)" }
if (-not $AgentKey) { throw "AgentKey is required (-AgentKey <key> or `$env:JT_IPAM_AGENT_KEY)" }
$Server = $Server.TrimEnd("/")

# Same reason as in the agent: PowerShell 5.1 may still default to TLS 1.0.
try {
    $proto = [Net.SecurityProtocolType]::Tls12
    if ([Enum]::GetNames([Net.SecurityProtocolType]) -contains "Tls13") { $proto = $proto -bor [Net.SecurityProtocolType]::Tls13 }
    [Net.ServicePointManager]::SecurityProtocol = $proto
} catch { }
function Enable-JtPin {
    <# Only trust the jt-ipam server whose certificate has this SHA-256 (hex). Works with a self-signed
       server certificate; any other server -- including one posing as jt-ipam -- is refused before the
       agent key is sent. The check is compiled C#: a PowerShell script block as the callback can run on a
       thread without a runspace and fail. #>
    param([string] $Sha256)
    if (-not ("JtPin" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Net.Security;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
public static class JtPin {
    public static string Sha256 = "";
    public static bool Check(object sender, X509Certificate cert, X509Chain chain, SslPolicyErrors errors) {
        if (cert == null) return false;
        using (SHA256 h = SHA256.Create()) {
            string got = BitConverter.ToString(h.ComputeHash(cert.GetRawCertData())).Replace("-", "");
            return string.Equals(got, Sha256.Replace(":", ""), StringComparison.OrdinalIgnoreCase);
        }
    }
}
"@
    }
    [JtPin]::Sha256 = $Sha256
    [Net.ServicePointManager]::ServerCertificateValidationCallback =
        [Delegate]::CreateDelegate([Net.Security.RemoteCertificateValidationCallback], [JtPin].GetMethod("Check"))
}

if ($CertSha256) { Enable-JtPin -Sha256 $CertSha256 }
elseif ($NoVerifyTls) { [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } }

Write-Step "Creating directories"
foreach ($d in @($INSTALL_DIR, $DATA_DIR)) {
    if (-not (Test-Path -LiteralPath $d)) { $null = New-Item -ItemType Directory -Path $d -Force }
}

Write-Step "Downloading agent from $Server"
$resp = Invoke-WebRequest -Uri "$Server/api/v1/cert-agents/agent.ps1" -UseBasicParsing -TimeoutSec 30
[IO.File]::WriteAllBytes($AGENT_PATH, $resp.RawContentStream.ToArray())

if (Test-Path -LiteralPath $CONFIG_PATH) {
    Write-Step "Config already exists, keeping it ($CONFIG_PATH)"
    if ($CertSha256) {
        # Re-running the install command is how an existing agent gets the server pin; deployments stay.
        $lines = @(Get-Content -LiteralPath $CONFIG_PATH | Where-Object { $_ -notmatch '^CERT_SHA256=' })
        $out = New-Object Collections.Generic.List[string]
        foreach ($l in $lines) { $out.Add($l); if ($l -match '^AGENT_KEY=') { $out.Add("CERT_SHA256=$CertSha256") } }
        Set-Content -LiteralPath $CONFIG_PATH -Value $out -Encoding UTF8
        Write-Step "Pinned the jt-ipam server certificate in the existing config"
    }
} else {
    Write-Step "Writing config"
    $verify = if ($NoVerifyTls -and -not $CertSha256) { "false" } else { "true" }
    $pinLine = if ($CertSha256) { "CERT_SHA256=$CertSha256" } else { "# CERT_SHA256=<SHA-256 of the jt-ipam server certificate>  (pin; works with a self-signed server)" }
    $config = @"
# jt-ipam certificate distribution agent configuration.
SERVER=$Server
AGENT_KEY=$AgentKey
$pinLine
VERIFY_TLS=$verify
AUTO_UPDATE=true
# Delete the previously bound certificate from the store after a successful switch.
# Off by default: the same certificate may still be bound elsewhere on this machine.
REMOVE_OLD_CERT=false

# --- What to deploy -------------------------------------------------------------
# One group of DEPLOY_<N>_* lines per deployment. N = 1, 2, 3, ...
#
# IIS: import the certificate and point an existing HTTPS binding at it.
#   DEPLOY_1_CERT=wildcard-example-com
#   DEPLOY_1_PROFILE=iis
#   DEPLOY_1_BINDING=0.0.0.0:443           # or www.example.com:443 for an SNI binding
#
# Certificate store only (Exchange, RD Gateway, your own apps):
#   DEPLOY_2_CERT=wildcard-example-com
#   DEPLOY_2_PROFILE=store
#
# Plain files (for software that reads PEM or PFX from disk):
#   DEPLOY_3_CERT=wildcard-example-com
#   DEPLOY_3_PROFILE=files
#   DEPLOY_3_FULLCHAIN=C:\apps\ssl\site.pem
#   DEPLOY_3_KEY=C:\apps\ssl\site.key
#   DEPLOY_3_RELOAD=powershell -Command "Restart-Service MyApp"
"@
    Set-Content -LiteralPath $CONFIG_PATH -Value $config -Encoding UTF8
}

Write-Step "Restricting permissions on the config (it holds the agent key)"
# Well-known SIDs rather than names, which are localized on non-English Windows.
$acl = Get-Acl -LiteralPath $CONFIG_PATH
$acl.SetAccessRuleProtection($true, $false)   # stop inheriting; drop inherited entries
foreach ($rule in @($acl.Access)) { $null = $acl.RemoveAccessRule($rule) }
foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {   # SYSTEM, Administrators
    $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
        (New-Object Security.Principal.SecurityIdentifier $sid), "FullControl", "Allow")))
}
Set-Acl -LiteralPath $CONFIG_PATH -AclObject $acl

Write-Step "Registering the daily scheduled task ($Time)"
# Register-ScheduledTask rather than schtasks.exe: schtasks takes the whole command as a
# single /TR argument, and its quoting rules mangle any path containing a space -- which the
# default install path under "C:\Program Files" always does. Register-ScheduledTask passes
# the argument string through verbatim, so the embedded quotes survive.
# cmd.exe is still the executable so that the >> redirection into the log works.
$taskArgs = "/c powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$AGENT_PATH"" >> ""$LOG_PATH"" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $taskArgs

# The Linux agent runs from a systemd timer with RandomizedDelaySec=600 and Persistent=true.
# These are the same two properties: spread the load so every host does not hit the server on
# the same second, and catch up a run that was missed because the machine was off.
$trigger = New-ScheduledTaskTrigger -Daily -At $Time -RandomDelay (New-TimeSpan -Minutes 10)

# Task Scheduler has no systemd equivalent for these, and its defaults are wrong for us:
# it refuses to start a task on battery and stops one that switches to battery, which would
# silently skip renewals on a laptop or a VM that reports a battery. ExecutionTimeLimit
# defaults to three days -- a hung run would block every later one until then.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

# By SID, not "SYSTEM"/"NT AUTHORITY\SYSTEM": those names are localized on a non-English
# Windows, and the account would not resolve.
$principal = New-ScheduledTaskPrincipal -UserId "S-1-5-18" -LogonType ServiceAccount -RunLevel Highest
$null = Register-ScheduledTask -TaskName $TASK_NAME -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "jt-ipam certificate distribution" -Force

Write-Host ""
Write-Host "Installed." -ForegroundColor Green
Write-Host "  Agent   : $AGENT_PATH"
Write-Host "  Config  : $CONFIG_PATH   <- add your DEPLOY_1_* lines here"
Write-Host "  Log     : $LOG_PATH"
Write-Host "  Schedule: daily at $Time (task '$TASK_NAME', runs as SYSTEM)"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit $CONFIG_PATH and add the certificate(s) to deploy."
Write-Host "  2. Dry run:  powershell -ExecutionPolicy Bypass -File `"$AGENT_PATH`" -DryRun -DebugLog"
Write-Host "  3. Run now:  schtasks /Run /TN $TASK_NAME"
Write-Host ""
Write-Host "Uninstall:   .\jt-ipam-cert-agent-installer.ps1 -Uninstall"
