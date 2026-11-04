<#
.SYNOPSIS
  jt-ipam certificate distribution agent for Windows (IIS and friends).

.DESCRIPTION
  Windows counterpart of jt_ipam_cert_agent.sh. Periodically asks jt-ipam whether the
  certificates this machine is responsible for have a newer version; if so it imports the
  new certificate into the Windows certificate store and re-points the IIS HTTPS binding at
  it, verifies the switch by actually opening a TLS connection, rolls back on failure, and
  reports the result back to jt-ipam.

  Requirements: Windows PowerShell 5.1 (ships with Windows Server 2019+) run as
  Administrator. No extra modules, no Python, no OpenSSL.

  Why this differs from the Linux agent: IIS does not read certificates from files. It binds
  a certificate held in the Windows certificate store, by thumbprint. So instead of "write
  files, test config, reload", the flow is "import to store, repoint binding, verify, roll
  back".

  Config file (default C:\ProgramData\jt-ipam-cert-agent\config, KEY=VALUE, one per line):

    SERVER=https://ipam.example.com
    AGENT_KEY=<enrollment key>
    VERIFY_TLS=true             # set false only if the jt-ipam server cert is self-signed
    AUTO_UPDATE=true            # auto-update this script when the server has a newer one
    REMOVE_OLD_CERT=false       # delete the previously bound cert from the store after a
                                # successful switch. Off by default: the same certificate
                                # may still be bound elsewhere on this machine.

    # Each deployment is a group of DEPLOY_<N>_* lines. N = 1, 2, 3, ...
    #
    #   DEPLOY_1_CERT=wildcard-example-com     # certificate name in jt-ipam
    #   DEPLOY_1_PROFILE=iis                   # iis | store | files
    #
    # iis    Import into LocalMachine\My, then point an HTTPS binding at it.
    #        DEPLOY_1_BINDING=0.0.0.0:443              non-SNI: all addresses, port 443
    #        DEPLOY_1_BINDING=www.example.com:443      SNI: that hostname on port 443
    #        The binding must already exist in IIS -- this agent swaps the certificate, it
    #        does not create or modify site configuration.
    #        DEPLOY_1_PROBE=127.0.0.1                  optional: address used to verify the
    #                                                  switch (default 127.0.0.1)
    #        DEPLOY_1_RELOAD=iisreset /noforce         optional: usually unnecessary, the
    #                                                  binding change takes effect at once
    #
    # store  Import into LocalMachine\My only. For services you point at a thumbprint
    #        yourself (Exchange, RD Gateway, third-party apps).
    #
    # files  Write certificate files to paths you choose, then run an optional command.
    #        DEPLOY_1_FULLCHAIN=C:\apps\ssl\site.pem   DEPLOY_1_KEY=C:\apps\ssl\site.key
    #        DEPLOY_1_CRT=  DEPLOY_1_CHAIN=  DEPLOY_1_COMBINED=  DEPLOY_1_PFX=
    #        DEPLOY_1_PFX_PASSWORD=secret              password for the .pfx it writes
    #        DEPLOY_1_RELOAD=Restart-Service MyApp

.EXAMPLE
  .\jt_ipam_cert_agent.ps1 -DryRun
  Show what would be imported and rebound, change nothing.
#>

param(
    [string] $Config,
    [switch] $DryRun,
    [switch] $Force,
    [switch] $DebugLog,
    [switch] $Upgrade,
    [switch] $ShowVersion,
    [switch] $Help
)

$AGENT_VERSION = "1.1.1"

# Public key (RSA) that signs this script: self-update only installs a copy whose signature verifies, so
# neither a fake server nor a compromised one can push a script that runs as SYSTEM.
$AGENT_SIGNING_MODULUS = "58yoM59293eHgMFKcrE6nWfpIWVQqHr7SJIhyXmCjaAQVoqwCY2VNqinJIMGGDE2wAiKKjxm8xSi+0Cx89/fF/hNAs59s+T9m8q4pV7f39QW/AeREcMj+1qk3Y+f4XbarAh2kd82f9eNx4mQ7P+1AIpyFTfPRsX/lzcNVZ8L9PcwgOA08qydmiuJ0yLNR987OMaWuc2wRIXjcYmwjYLXrpqxDC+jCccY2Ni25nCrJzJIoqGbhWJP5Ddu3lq/gD37DiNoqmleNYiDeEAM1/w7LtplmMF0Pn4QsMvcRzgORMwYoBFrT09ouC3iKiIcZNlDnlg9dBvpE1QmyY2EwLVxS7Y+FmMPyfgTvKQyT2s63+3KrXhR3nOESdfolZdbjW09dEybI6TPSRjAlQKl7cuIKyJeySlxeFu4ARscP6HcmoBNVpqLZP2epoDz0mORCeGG9U2K4yDDHZwrmI+U+/BvO7LX+hO8YXbYeONCuztgg1MiwFyWdPk+xg0/jC651a2N"
$AGENT_SIGNING_EXPONENT = "AQAB"

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

$SELF = $MyInvocation.MyCommand.Path
$DEFAULT_CONFIG = "C:\ProgramData\jt-ipam-cert-agent\config"
$STATE_DIR = "C:\ProgramData\jt-ipam-cert-agent"
$STATE_FILE = Join-Path $STATE_DIR "state"

# The application id IIS uses for its own http.sys SSL registrations. Reusing it keeps the
# binding looking normal in IIS Manager instead of showing up as an orphaned reservation.
$IIS_APPID = "{4dc3e181-e14b-4a21-b022-59fc669b0914}"

function Write-Log { param([string] $Message) Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message) }

function Invoke-Native {
    <# Run an external command and return its combined output plus exit code.

       Needed because $ErrorActionPreference = "Stop" plus `2>&1` on a native command turns
       anything the command writes to stderr into a *terminating* error in Windows
       PowerShell 5.1 -- so `netsh http delete sslcert` on a binding that does not exist yet
       would abort the whole run instead of being the harmless no-op it should be. #>
    param([string] $File, [string[]] $Arguments)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $File @Arguments 2>&1
        $rc = $LASTEXITCODE          # capture before anything else can touch it
        # Native tools can emit a screenful of localized text plus a PowerShell error record.
        # Keep the first few meaningful lines: enough to act on, short enough for a report field.
        $text = ($out | Out-String).Trim()
        $lines = @($text -split "`r?`n" | Where-Object { $_.Trim() -and $_ -notmatch "^\s*\+|\.ps1:\d+|CategoryInfo|FullyQualifiedErrorId" })
        $brief = ($lines | Select-Object -First 3) -join " / "
        if ($brief.Length -gt 300) { $brief = $brief.Substring(0, 300) + "..." }
        return [pscustomobject]@{ Output = $text; Brief = $brief; ExitCode = $rc }
    } finally { $ErrorActionPreference = $prev }
}
function Write-Dbg { param([string] $Message) if ($DebugLog) { Write-Log "[debug] $Message" } }

function Show-Usage {
    Write-Host @"
jt-ipam certificate distribution agent for Windows v$AGENT_VERSION

Usage: jt_ipam_cert_agent.ps1 [options]

Options:
  -Config PATH    Config file (default: $DEFAULT_CONFIG)
  -DryRun         Show what would be imported / rebound; make no changes
  -Force          Re-deploy even if the certificate is already up to date
  -DebugLog       Verbose output
  -Upgrade        Update this agent to the server's latest version, then exit
                  (works even if AUTO_UPDATE=false in the config)
  -ShowVersion    Print the agent version and exit
  -Help           Show this help and exit

Must be run as Administrator: importing into LocalMachine\My and changing http.sys SSL
bindings both require it.
Scheduled-run log: $STATE_DIR\last-run.log
"@
}

if ($Help) { Show-Usage; exit 0 }
if ($ShowVersion) { Write-Output $AGENT_VERSION; exit 0 }

# ─────────────────── Config ───────────────────

function Read-ConfigFile {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { throw "cannot read config file $Path" }
    $cfg = @{}
    foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
        $t = $line.Trim()
        if ($t.Length -eq 0 -or $t.StartsWith("#")) { continue }
        $i = $t.IndexOf("=")
        if ($i -lt 1) { continue }
        $k = $t.Substring(0, $i).Trim()
        $v = $t.Substring($i + 1).Trim()
        # Allow quoting so values may contain leading/trailing spaces or a '#'
        if ($v.Length -ge 2 -and (($v[0] -eq '"' -and $v[-1] -eq '"') -or ($v[0] -eq "'" -and $v[-1] -eq "'"))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $cfg[$k] = $v
    }
    return $cfg
}

function Get-Cfg {
    param([hashtable] $Cfg, [string] $Key, [string] $Default = "")
    if ($Cfg.ContainsKey($Key) -and $Cfg[$Key] -ne "") { return $Cfg[$Key] }
    return $Default
}

function Test-CfgTrue { param([string] $Value) return @("true", "1", "yes", "on") -contains $Value.ToLowerInvariant() }

if (-not $Config) { $Config = $DEFAULT_CONFIG }

# Startup problems are the operator's to fix (missing or incomplete config), so report them
# as one readable line. Letting the exception escape prints a PowerShell stack trace with the
# offending source line, which buries the actual message.
try {
    $CFG = Read-ConfigFile -Path $Config
    $SERVER = (Get-Cfg $CFG "SERVER").TrimEnd("/")
    $AGENT_KEY = Get-Cfg $CFG "AGENT_KEY"
    if (-not $SERVER) { throw "config is missing SERVER (e.g. SERVER=https://ipam.example.com)" }
    if (-not $AGENT_KEY) { throw "config is missing AGENT_KEY (copy it from the jt-ipam cert agent page)" }
} catch {
    Write-Host "error: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "       config file: $Config"
    exit 2
}
$VERIFY_TLS = Test-CfgTrue (Get-Cfg $CFG "VERIFY_TLS" "true")
$CERT_SHA256 = "$(Get-Cfg $CFG "CERT_SHA256" "")".Trim()
# How the agent makes sure it talks to the real jt-ipam (reported to the server, shown on the agent list).
$TLS_MODE = if ($CERT_SHA256) { "pinned" } elseif (-not $VERIFY_TLS) { "insecure" } else { "ca" }
$AUTO_UPDATE = Test-CfgTrue (Get-Cfg $CFG "AUTO_UPDATE" "true")
$REMOVE_OLD_CERT = Test-CfgTrue (Get-Cfg $CFG "REMOVE_OLD_CERT" "false")
$API = "$SERVER/api/v1/cert-agents"

# PowerShell 5.1 still negotiates TLS 1.0 by default on older builds, which modern servers
# refuse. Opt in to 1.2 (and 1.3 where the platform knows about it) explicitly.
try {
    $proto = [Net.SecurityProtocolType]::Tls12
    if ([Enum]::GetNames([Net.SecurityProtocolType]) -contains "Tls13") { $proto = $proto -bor [Net.SecurityProtocolType]::Tls13 }
    [Net.ServicePointManager]::SecurityProtocol = $proto
} catch { Write-Dbg "could not raise SecurityProtocol: $($_.Exception.Message)" }

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

if ($CERT_SHA256) {
    Write-Dbg "pinned to the jt-ipam server certificate $CERT_SHA256"
    Enable-JtPin -Sha256 $CERT_SHA256
} elseif (-not $VERIFY_TLS) {
    Write-Dbg "TLS verification disabled for calls to $SERVER"
    [Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
}

# ─────────────────── HTTP ───────────────────

function Invoke-Jt {
    <# Returns the raw response body as a byte[]. Used for both text and binary parts so the
       PKCS#12 blob never has to touch the disk. #>
    param([string] $Url, [hashtable] $ExtraHeaders = @{})
    $headers = @{ "X-Agent-Key" = $AGENT_KEY; "X-Agent-Version" = "$AGENT_VERSION-win"; "X-Agent-Tls" = $TLS_MODE }
    foreach ($k in $ExtraHeaders.Keys) { $headers[$k] = $ExtraHeaders[$k] }
    Write-Dbg "GET $Url"
    $r = Invoke-WebRequest -Uri $Url -Headers $headers -UseBasicParsing -TimeoutSec 30
    return $r.RawContentStream.ToArray()
}

function Invoke-JtText { param([string] $Url) return [Text.Encoding]::UTF8.GetString((Invoke-Jt -Url $Url)) }

# ─────────────────── Certificate store ───────────────────

function Import-JtCertificate {
    <# Import a PKCS#12 blob into LocalMachine\My straight from memory and return its
       thumbprint. Deliberately never writes the PFX to disk: it holds the private key, and a
       temp file would leave it readable on the filesystem until the file is cleaned up. #>
    param([byte[]] $Pfx, [string] $Password, [string] $StoreName = "My")
    $flags = [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::MachineKeySet -bor `
             [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::PersistKeySet -bor `
             [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable
    $cert = [Security.Cryptography.X509Certificates.X509Certificate2]::new($Pfx, $Password, $flags)
    # StoreName is normally "My". A domain controller serving LDAPS reads its certificate
    # from the service store "NTDS\My" instead -- putting it in LocalMachine\My does nothing.
    $store = [Security.Cryptography.X509Certificates.X509Store]::new($StoreName, "LocalMachine")
    $store.Open("ReadWrite")
    try { $store.Add($cert) } finally { $store.Close() }
    return $cert.Thumbprint
}

function Remove-JtCertificate {
    param([string] $Thumbprint)
    if (-not $Thumbprint) { return }
    $store = [Security.Cryptography.X509Certificates.X509Store]::new("My", "LocalMachine")
    $store.Open("ReadWrite")
    try {
        foreach ($c in @($store.Certificates)) {
            if ($c.Thumbprint -eq $Thumbprint) { $store.Remove($c) }
        }
    } finally { $store.Close() }
}

# ─────────────────── Bindings ───────────────────

function ConvertTo-BindingParts {
    <# "0.0.0.0:443" -> non-SNI ipport ; "www.example.com:443" -> SNI hostnameport.
       An address that parses as an IP (or the * wildcard) is an ipport binding; anything
       else is a hostname, which http.sys registers separately as hostnameport. #>
    param([string] $Binding)
    $i = $Binding.LastIndexOf(":")
    if ($i -lt 1) { throw "binding must look like <address>:<port>, got '$Binding'" }
    $addr = $Binding.Substring(0, $i).Trim()
    $port = $Binding.Substring($i + 1).Trim()
    if ($addr -eq "*") { $addr = "0.0.0.0" }
    $isIp = $true
    try { $null = [Net.IPAddress]::Parse($addr) } catch { $isIp = $false }
    $key = if ($isIp) { "ipport=${addr}:${port}" } else { "hostnameport=${addr}:${port}" }
    return [pscustomobject]@{
        Address = $addr
        Port    = [int] $port
        IsSni   = (-not $isIp)
        Key     = $key
    }
}

function Get-ServedThumbprint {
    <# Open a TLS connection and return the thumbprint of the certificate actually served.
       This is how the agent both records the certificate to roll back to and confirms the
       switch really took effect -- it is locale independent (unlike parsing netsh output)
       and it checks the observable outcome rather than trusting a command's exit code. #>
    param([string] $TargetHost, [int] $Port, [string] $SniHost)
    $client = $null; $ssl = $null
    try {
        $client = New-Object Net.Sockets.TcpClient
        $iar = $client.BeginConnect($TargetHost, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(5000)) { return $null }
        $client.EndConnect($iar)
        $ssl = New-Object Net.Security.SslStream($client.GetStream(), $false, { $true })
        $ssl.AuthenticateAsClient($SniHost)
        if (-not $ssl.RemoteCertificate) { return $null }
        $c = [Security.Cryptography.X509Certificates.X509Certificate2]::new($ssl.RemoteCertificate)
        return $c.Thumbprint
    } catch {
        Write-Dbg "TLS probe ${TargetHost}:${Port} (sni=$SniHost) failed: $($_.Exception.Message)"
        return $null
    } finally {
        if ($ssl) { $ssl.Dispose() }
        if ($client) { $client.Close() }
    }
}

function Get-HttpSysCertificate {
    <# The thumbprint http.sys has registered for *this specific* binding, or $null.

       Why this cannot be inferred from a TLS probe: an SNI binding with no registration is
       still answered, by the catch-all non-SNI binding on the same port. Probing therefore
       reports the fallback certificate, and if that happens to be the certificate we are
       deploying, "is it already bound?" answers yes for a binding that has nothing bound at
       all -- the agent then reports success without registering anything. That is exactly
       what happened on a two-SNI-site IIS host.

       netsh output is localized, so this matches the 40-hex-digit thumbprint *value* rather
       than any label. The appid GUID in the same output is dash-separated, so it cannot
       produce a 40-character run. #>
    param([object] $Parts)
    $r = Invoke-Native "netsh" @("http", "show", "sslcert", $Parts.Key)
    $m = [regex]::Match($r.Output, "(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")
    if ($m.Success) { return $m.Value.ToUpperInvariant() }
    return $null
}

function Set-HttpSysCertificate {
    <# Point an http.sys SSL binding at a thumbprint. Uses netsh rather than the
       WebAdministration module because netsh is always present (the IIS PowerShell provider
       is a separately installable feature), and delete+add avoids having to parse netsh's
       output, which is localized. #>
    param([object] $Parts, [string] $Thumbprint)
    $null = Invoke-Native "netsh" @("http", "delete", "sslcert", $Parts.Key)   # absent is fine
    # Named $netshArgs because $args is an automatic variable.
    $netshArgs = @("http", "add", "sslcert", $Parts.Key, "certhash=$Thumbprint",
                   "appid=$IIS_APPID", "certstorename=MY")
    $r = Invoke-Native "netsh" $netshArgs
    if ($r.ExitCode -ne 0) { throw "netsh add sslcert failed: $($r.Brief)" }
    Write-Dbg "netsh add sslcert $($Parts.Key) -> $Thumbprint"
}

# ─────────────────── Reporting ───────────────────

$script:ReportLines = New-Object Collections.ArrayList

function Add-Report {
    param([string] $Cert, [string] $Profile, [string] $Status,
          [string] $Fingerprint, [string] $NotAfter, [string] $Message = "")
    $msg = ($Message -replace "`t", " ") -replace "`r?`n", " "
    $dry = if ($DryRun) { "1" } else { "0" }
    $null = $script:ReportLines.Add(("{0}`t{1}`t{2}`t{3}`t{4}`t{5}`t{6}" -f `
        $Cert, $Profile, $Status, $Fingerprint, $NotAfter, $dry, $msg))
}

function Send-Report {
    if ($script:ReportLines.Count -eq 0) { return }
    $body = ($script:ReportLines -join "`n") + "`n"
    try {
        $headers = @{ "X-Agent-Key" = $AGENT_KEY; "X-Agent-Version" = "$AGENT_VERSION-win"; "X-Agent-Tls" = $TLS_MODE }
        $null = Invoke-WebRequest -Uri "$API/report" -Method Post -Headers $headers `
            -ContentType "text/plain" -Body ([Text.Encoding]::UTF8.GetBytes($body)) `
            -UseBasicParsing -TimeoutSec 30
    } catch {
        Write-Log "report failed (does not affect deployment): $($_.Exception.Message)"
    }
}

# ─────────────────── State ───────────────────

function Get-DeploymentTarget {
    <# What makes this deployment distinct from another one using the same certificate and
       profile. Without it the state key would be just cert+profile, and a second binding
       served by the same certificate -- two SNI sites on 443, say -- would be skipped as
       "already up to date" forever: silently never renewed, while reporting ok. #>
    param([hashtable] $D, [string] $Profile)
    switch ($Profile) {
        "iis"   { return (Get-Cfg $D "BINDING" "0.0.0.0:443") }
        "files" { return (@("CRT", "CHAIN", "FULLCHAIN", "KEY", "COMBINED", "PFX") |
                          ForEach-Object { Get-Cfg $D $_ }) -join "|" }
        "winrm" { return "winrm:" + (Get-Cfg $D "PORT" "5986") }
        "rdp"   { return "rdp:" + (Get-Cfg $D "PORT" "3389") }
        "store" { return (Get-Cfg $D "STORE" "My") }
        default { return "" }
    }
}

function Get-StateFingerprint {
    param([string] $Cert, [string] $Profile, [string] $Target)
    if (-not (Test-Path -LiteralPath $STATE_FILE)) { return "" }
    foreach ($line in (Get-Content -LiteralPath $STATE_FILE -Encoding UTF8)) {
        $p = $line -split "`t"
        # 4 columns is the current format. A 3-column line was written by an older agent and
        # has no target recorded, so only trust it when this deployment has no target either.
        if ($p.Count -ge 4 -and $p[0] -eq $Cert -and $p[1] -eq $Profile -and $p[2] -eq $Target) { return $p[3] }
        if ($p.Count -eq 3 -and $p[0] -eq $Cert -and $p[1] -eq $Profile -and $Target -eq "") { return $p[2] }
    }
    return ""
}

function Set-StateFingerprint {
    param([string] $Cert, [string] $Profile, [string] $Target, [string] $Fingerprint)
    $keep = @()
    if (Test-Path -LiteralPath $STATE_FILE) {
        foreach ($line in (Get-Content -LiteralPath $STATE_FILE -Encoding UTF8)) {
            $p = $line -split "`t"
            if ($p.Count -ge 4 -and $p[0] -eq $Cert -and $p[1] -eq $Profile -and $p[2] -eq $Target) { continue }
            if ($p.Count -eq 3 -and $p[0] -eq $Cert -and $p[1] -eq $Profile) { continue }  # replace old-format line
            if ($line.Trim()) { $keep += $line }
        }
    }
    $keep += ("{0}`t{1}`t{2}`t{3}" -f $Cert, $Profile, $Target, $Fingerprint)
    Set-Content -LiteralPath $STATE_FILE -Value $keep -Encoding UTF8
}

# ─────────────────── Deployment ───────────────────

function Get-CertificatePfx {
    <# Fetch the certificate as a Windows-importable PKCS#12. The password is random per run
       and only ever lives in memory: it exists so the server can hand back a PBESv1 (legacy)
       PFX, the form every version of the Windows CryptoAPI accepts. The blob is decrypted and
       imported immediately and never touches the disk, so the weaker algorithm guards nothing
       an attacker could reach -- see export_cert_file() on the server for the full reasoning. #>
    param([string] $Cert)
    $bytes = New-Object byte[] 24
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $pw = [Convert]::ToBase64String($bytes)
    $url = "$API/bundle/raw?cert=$([Uri]::EscapeDataString($Cert))&part=pkcs12"
    $pfx = Invoke-Jt -Url $url -ExtraHeaders @{ "X-Pfx-Password" = $pw }
    return [pscustomobject]@{ Bytes = $pfx; Password = $pw }
}

function Invoke-DeployIis {
    param([hashtable] $D, [string] $Cert, [string] $Fingerprint, [string] $NotAfter)

    $binding = Get-Cfg $D "BINDING" "0.0.0.0:443"
    $parts = ConvertTo-BindingParts -Binding $binding
    $probeHost = Get-Cfg $D "PROBE" "127.0.0.1"
    # For an SNI binding the hostname is what selects the certificate, so it must be what we
    # send as SNI when probing; otherwise http.sys hands back a different (or no) certificate.
    $sniHost = if ($parts.IsSni) { $parts.Address } else { Get-Cfg $D "PROBE_HOST" $probeHost }

    if ($DryRun) {
        Write-Log "[$Cert/iis] would import and bind to $($parts.Key)"
        Add-Report $Cert "iis" "dry-run" $Fingerprint $NotAfter "would rebind $($parts.Key)"
        return $true
    }

    # What this binding actually has registered -- not what the port happens to serve.
    $old = Get-HttpSysCertificate -Parts $parts
    Write-Dbg "registered on $($parts.Key): $(if ($old) { $old } else { '(nothing)' })"

    $bundle = Get-CertificatePfx -Cert $Cert
    $thumb = Import-JtCertificate -Pfx $bundle.Bytes -Password $bundle.Password
    Write-Log "[$Cert/iis] imported into LocalMachine\My ($thumb)"

    if ($old -eq $thumb) {
        Add-Report $Cert "iis" "ok" $Fingerprint $NotAfter "already bound on $($parts.Key)"
        return $true
    }

    try {
        Set-HttpSysCertificate -Parts $parts -Thumbprint $thumb
    } catch {
        # delete succeeded but add did not: the binding is now gone and the site is serving
        # nothing on that port. Put the previous certificate back before giving up.
        $why = $_.Exception.Message
        if ($old) {
            try {
                Set-HttpSysCertificate -Parts $parts -Thumbprint $old
                Write-Log "[$Cert/iis] rebind failed ($why), restored the previous certificate"
                Add-Report $Cert "iis" "failed" $Fingerprint $NotAfter "rebind failed ($why), restored previous"
            } catch {
                Write-Log "[$Cert/iis] rebind failed AND restore failed -- $($parts.Key) has no certificate bound: $why"
                Add-Report $Cert "iis" "failed" $Fingerprint $NotAfter "rebind and restore both failed: $why"
            }
        } else {
            Write-Log "[$Cert/iis] rebind failed: $why"
            Add-Report $Cert "iis" "failed" $Fingerprint $NotAfter "rebind failed: $why"
        }
        return $false
    }

    $reload = Get-Cfg $D "RELOAD"
    if ($reload) {
        Write-Dbg "reload: $reload"
        Write-Dbg "reload output: $((Invoke-Native "cmd.exe" @("/c", $reload)).Output)"
    }

    $now = Get-ServedThumbprint -TargetHost $probeHost -Port $parts.Port -SniHost $sniHost
    if ($now -eq $thumb) {
        Write-Log "[$Cert/iis] bound and verified on $($parts.Key)"
        if ($REMOVE_OLD_CERT -and $old -and $old -ne $thumb) {
            Remove-JtCertificate -Thumbprint $old
            Write-Dbg "removed previous certificate $old from the store"
        }
        Add-Report $Cert "iis" "ok" $Fingerprint $NotAfter "bound on $($parts.Key)"
        return $true
    }

    # Verification failed. Put back whatever was being served before we touched anything.
    $detail = if ($now) { "serving $now" } else { "no TLS response on ${probeHost}:$($parts.Port)" }
    if ($old) {
        try {
            Set-HttpSysCertificate -Parts $parts -Thumbprint $old
            Write-Log "[$Cert/iis] verification failed ($detail), rolled back to $old"
            Add-Report $Cert "iis" "failed" $Fingerprint $NotAfter "verify failed ($detail), rolled back"
        } catch {
            Write-Log "[$Cert/iis] verification failed AND rollback failed: $($_.Exception.Message)"
            Add-Report $Cert "iis" "failed" $Fingerprint $NotAfter "verify failed ($detail), rollback failed"
        }
    } else {
        # Nothing was being served before, so there is no previous certificate to restore --
        # but we did create an http.sys registration, so take it back out rather than leaving
        # a reservation on a port no site answers on. Most likely cause: the admin has not
        # created the HTTPS binding in IIS yet, so say that instead of just "failed".
        $null = Invoke-Native "netsh" @("http", "delete", "sslcert", $parts.Key)
        Write-Log "[$Cert/iis] cannot verify ($detail) -- does IIS have an HTTPS binding on $($parts.Key)?"
        Add-Report $Cert "iis" "failed" $Fingerprint $NotAfter "cannot verify ($detail); is there an IIS HTTPS binding on $($parts.Key)?"
    }
    return $false
}

function Invoke-LdapsReload {
    <# Tell a domain controller to reload its LDAPS certificate.

       Dropping the certificate into NTDS\My is not enough on its own: the DC keeps serving
       the old one until it rotates, which can take hours. This rootDSE operation is the
       documented way to make it pick the new one up immediately. #>
    try {
        Add-Type -AssemblyName System.DirectoryServices.Protocols -ErrorAction Stop
        $conn = New-Object DirectoryServices.Protocols.LdapConnection("localhost")
        $conn.SessionOptions.ProtocolVersion = 3
        $req = New-Object DirectoryServices.Protocols.ModifyRequest(
            "", [DirectoryServices.Protocols.DirectoryAttributeOperation]::Replace,
            "renewServerCertificate", "1")
        $null = $conn.SendRequest($req)
        $conn.Dispose()
        Write-Log "[ldaps] asked the domain controller to reload its certificate"
    } catch {
        # Not fatal: the certificate is already in the store, the DC will get there on its own.
        Write-Log "[ldaps] could not trigger a reload ($($_.Exception.Message)); the DC will pick it up on its own"
    }
}

function Invoke-DeployWinrm {
    <# WinRM over HTTPS (5986).

       Worth its own profile because jt-ipam is itself a WinRM client: the Windows DNS and
       DHCP integrations talk to Windows hosts over 5986. Those listeners normally carry the
       self-signed certificate `winrm quickconfig` generates, which is why TLS verification
       usually has to be turned off at the jt-ipam end -- and which expires, taking remote
       management down at exactly the moment you need it to fix things. #>
    param([hashtable] $D, [string] $Cert, [string] $Fingerprint, [string] $NotAfter)
    $port = [int] (Get-Cfg $D "PORT" "5986")
    $probeHost = Get-Cfg $D "PROBE" "127.0.0.1"

    if ($DryRun) {
        Write-Log "[$Cert/winrm] would import and point the HTTPS listener at it"
        Add-Report $Cert "winrm" "dry-run" $Fingerprint $NotAfter "would rebind listener :$port"
        return $true
    }

    $old = Get-ServedThumbprint -TargetHost $probeHost -Port $port -SniHost $probeHost
    $bundle = Get-CertificatePfx -Cert $Cert
    $thumb = Import-JtCertificate -Pfx $bundle.Bytes -Password $bundle.Password
    Write-Log "[$Cert/winrm] imported into LocalMachine\My ($thumb)"
    if ($old -eq $thumb) {
        Add-Report $Cert "winrm" "ok" $Fingerprint $NotAfter "already in use on :$port"
        return $true
    }

    $sel = "winrm/config/Listener?Address=*+Transport=HTTPS"
    $r = Invoke-Native "winrm.cmd" @("set", $sel, "@{CertificateThumbprint=`"$thumb`"}")
    if ($r.ExitCode -ne 0) {
        # `set` fails when there is no HTTPS listener yet -- the usual first-time case.
        Write-Dbg "set failed, creating an HTTPS listener instead: $($r.Output)"
        $r = Invoke-Native "winrm.cmd" @(
            "create", $sel,
            "@{Hostname=`"$env:COMPUTERNAME`";CertificateThumbprint=`"$thumb`"}")
    }
    if ($r.ExitCode -ne 0) {
        # 最常見的原因不是權限，是憑證不符合 WinRM 的要求：CN/SAN 必須含這台的主機名稱，
        # 且要有「伺服器驗證」EKU。單看 WSManFault 看不出這件事，所以明講。
        $why = "$($r.Brief) -- WinRM requires the certificate's CN/SAN to include this host's " +
               "name ($env:COMPUTERNAME) and to have the Server Authentication EKU"
        Write-Log "[$Cert/winrm] could not configure the listener: $why"
        Add-Report $Cert "winrm" "failed" $Fingerprint $NotAfter $why
        return $false
    }

    $now = Get-ServedThumbprint -TargetHost $probeHost -Port $port -SniHost $probeHost
    if ($now -eq $thumb) {
        Write-Log "[$Cert/winrm] listener on :$port now serving $thumb"
        Add-Report $Cert "winrm" "ok" $Fingerprint $NotAfter "listener :$port"
        return $true
    }
    $detail = if ($now) { "serving $now" } else { "no TLS response on ${probeHost}:$port" }
    if ($old) {
        $null = Invoke-Native "winrm.cmd" @("set", $sel, "@{CertificateThumbprint=`"$old`"}")
        Write-Log "[$Cert/winrm] verification failed ($detail), rolled back to $old"
        Add-Report $Cert "winrm" "failed" $Fingerprint $NotAfter "verify failed ($detail), rolled back"
    } else {
        Write-Log "[$Cert/winrm] cannot verify ($detail) -- is the WinRM HTTPS listener enabled?"
        Add-Report $Cert "winrm" "failed" $Fingerprint $NotAfter "cannot verify ($detail)"
    }
    return $false
}

function Invoke-DeployRdp {
    <# Remote Desktop (3389). Unlike IIS this has nothing to do with http.sys -- the
       thumbprint lives in WMI, so this profile writes there and then confirms over TLS. #>
    param([hashtable] $D, [string] $Cert, [string] $Fingerprint, [string] $NotAfter)
    $port = [int] (Get-Cfg $D "PORT" "3389")
    $probeHost = Get-Cfg $D "PROBE" "127.0.0.1"
    $ns = "root\CIMV2\TerminalServices"

    if ($DryRun) {
        Write-Log "[$Cert/rdp] would import and set the Remote Desktop certificate"
        Add-Report $Cert "rdp" "dry-run" $Fingerprint $NotAfter "would rebind :$port"
        return $true
    }

    $ts = Get-WmiObject -Class Win32_TSGeneralSetting -Namespace $ns `
            -Filter "TerminalName='RDP-tcp'" -ErrorAction SilentlyContinue
    if (-not $ts) {
        Write-Log "[$Cert/rdp] Remote Desktop settings not found (is Remote Desktop installed?)"
        Add-Report $Cert "rdp" "failed" $Fingerprint $NotAfter "Win32_TSGeneralSetting not available"
        return $false
    }
    $old = $ts.SSLCertificateSHA1Hash

    $bundle = Get-CertificatePfx -Cert $Cert
    $thumb = Import-JtCertificate -Pfx $bundle.Bytes -Password $bundle.Password
    Write-Log "[$Cert/rdp] imported into LocalMachine\My ($thumb)"
    if ($old -eq $thumb) {
        Add-Report $Cert "rdp" "ok" $Fingerprint $NotAfter "already in use"
        return $true
    }

    try {
        $ts.SSLCertificateSHA1Hash = $thumb
        $null = $ts.Put()
    } catch {
        Write-Log "[$Cert/rdp] could not set the certificate: $($_.Exception.Message)"
        Add-Report $Cert "rdp" "failed" $Fingerprint $NotAfter $_.Exception.Message
        return $false
    }

    Start-Sleep -Seconds 2      # the change is not picked up instantly
    $now = Get-ServedThumbprint -TargetHost $probeHost -Port $port -SniHost $probeHost
    if ($now -eq $thumb) {
        Write-Log "[$Cert/rdp] Remote Desktop on :$port now serving $thumb"
        Add-Report $Cert "rdp" "ok" $Fingerprint $NotAfter "bound on :$port"
        return $true
    }

    # A failed probe does not prove the change failed -- Remote Desktop may simply be
    # switched off on this host. Read the setting back before deciding to roll anything back.
    $check = (Get-WmiObject -Class Win32_TSGeneralSetting -Namespace $ns `
                -Filter "TerminalName='RDP-tcp'" -ErrorAction SilentlyContinue).SSLCertificateSHA1Hash
    if ($check -eq $thumb) {
        Write-Log "[$Cert/rdp] setting applied, but :$port did not serve it (is Remote Desktop enabled?)"
        Add-Report $Cert "rdp" "ok" $Fingerprint $NotAfter "applied; could not verify on :$port"
        return $true
    }
    if ($old) {
        $ts.SSLCertificateSHA1Hash = $old
        $null = $ts.Put()
    }
    Write-Log "[$Cert/rdp] could not apply, restored the previous certificate"
    Add-Report $Cert "rdp" "failed" $Fingerprint $NotAfter "could not apply, rolled back"
    return $false
}

function Invoke-DeployStore {
    param([hashtable] $D, [string] $Cert, [string] $Fingerprint, [string] $NotAfter)
    if ($DryRun) {
        Write-Log "[$Cert/store] would import into LocalMachine\My"
        Add-Report $Cert "store" "dry-run" $Fingerprint $NotAfter "would import"
        return $true
    }
    $storeName = Get-Cfg $D "STORE" "My"
    $bundle = Get-CertificatePfx -Cert $Cert
    $thumb = Import-JtCertificate -Pfx $bundle.Bytes -Password $bundle.Password -StoreName $storeName
    Write-Log "[$Cert/store] imported into LocalMachine\$storeName ($thumb)"

    # Once the certificate is in NTDS\My the domain controller still has to be told, or it
    # keeps serving the old one until it rotates on its own (which can be hours).
    if ($storeName -like "NTDS*") { Invoke-LdapsReload }

    $reload = Get-Cfg $D "RELOAD"
    if ($reload) { $null = Invoke-Native "cmd.exe" @("/c", $reload) }
    Add-Report $Cert "store" "ok" $Fingerprint $NotAfter "store=$storeName thumbprint $thumb"
    return $true
}

function Write-JtFile {
    <# Write via a temp file in the same directory and then move it into place, so a reader
       never sees a half-written certificate. #>
    param([string] $Path, [byte[]] $Content)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { $null = New-Item -ItemType Directory -Path $dir -Force }
    $tmp = "$Path.new"
    [IO.File]::WriteAllBytes($tmp, $Content)
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Set-PrivateFileAcl {
    <# Private-key files: SYSTEM + Administrators only, inheritance off. Without this they
       inherit the parent directory's ACL, which on ProgramData grants Users read access. #>
    param([string] $Path)
    try {
        $acl = Get-Acl -LiteralPath $Path
        $acl.SetAccessRuleProtection($true, $false)   # stop inheriting; drop inherited entries
        foreach ($rule in @($acl.Access)) { $null = $acl.RemoveAccessRule($rule) }
        # Well-known SIDs, not names: "Administrators" does not exist on a non-English
        # Windows (the group name is localized), so matching by name would grant nobody.
        foreach ($sid in @("S-1-5-18", "S-1-5-32-544")) {   # SYSTEM, Administrators
            $acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule(
                (New-Object Security.Principal.SecurityIdentifier $sid), "FullControl", "Allow")))
        }
        Set-Acl -LiteralPath $Path -AclObject $acl
    } catch { Write-Log "warning: could not tighten permissions on $Path : $($_.Exception.Message)" }
}

function Invoke-DeployFiles {
    param([hashtable] $D, [string] $Cert, [string] $Fingerprint, [string] $NotAfter)
    $map = @{ FULLCHAIN = "fullchain"; KEY = "key"; CHAIN = "chain"; CRT = "cert"; COMBINED = "combined" }
    $written = @()
    $secret = @("KEY", "COMBINED", "PFX")

    foreach ($field in @("CRT", "CHAIN", "FULLCHAIN", "KEY", "COMBINED")) {
        $path = Get-Cfg $D $field
        if (-not $path) { continue }
        if ($DryRun) { $written += "$path ($($map[$field]))"; continue }
        $data = Invoke-Jt -Url "$API/bundle/raw?cert=$([Uri]::EscapeDataString($Cert))&part=$($map[$field])"
        Write-JtFile -Path $path -Content $data
        if ($secret -contains $field) { Set-PrivateFileAcl -Path $path }
        $written += $path
    }

    $pfxPath = Get-Cfg $D "PFX"
    if ($pfxPath) {
        if ($DryRun) {
            $written += "$pfxPath (pkcs12)"
        } else {
            $pw = Get-Cfg $D "PFX_PASSWORD"
            $headers = @{}
            if ($pw) { $headers["X-Pfx-Password"] = $pw }
            $data = Invoke-Jt -Url "$API/bundle/raw?cert=$([Uri]::EscapeDataString($Cert))&part=pkcs12" -ExtraHeaders $headers
            Write-JtFile -Path $pfxPath -Content $data
            Set-PrivateFileAcl -Path $pfxPath
            $written += $pfxPath
        }
    }

    if ($written.Count -eq 0) {
        Add-Report $Cert "files" "skipped" $Fingerprint $NotAfter "no output paths configured"
        Write-Log "[$Cert/files] no DEPLOY_*_{CRT,CHAIN,FULLCHAIN,KEY,COMBINED,PFX} set, nothing to do"
        return $true
    }

    if ($DryRun) {
        Write-Log "[$Cert/files] would write: $($written -join ', ')"
        Add-Report $Cert "files" "dry-run" $Fingerprint $NotAfter "would write $($written.Count) file(s)"
        return $true
    }

    $reload = Get-Cfg $D "RELOAD"
    if ($reload) {
        $r = Invoke-Native "cmd.exe" @("/c", $reload)
        if ($r.ExitCode -ne 0) {
            Write-Log "[$Cert/files] wrote files but reload failed: $($r.Output)"
            Add-Report $Cert "files" "failed" $Fingerprint $NotAfter "reload failed: $($r.Brief)"
            return $false
        }
    }
    Write-Log "[$Cert/files] wrote $($written.Count) file(s)"
    Add-Report $Cert "files" "ok" $Fingerprint $NotAfter "wrote $($written -join ', ')"
    return $true
}

# ─────────────────── Self-update ───────────────────

function Get-FileSha256 {
    param([string] $Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return -join ($sha.ComputeHash([IO.File]::ReadAllBytes($Path)) | ForEach-Object { $_.ToString("x2") }) }
    finally { $sha.Dispose() }
}

function Test-JtSignature {
    <# $true when $Bytes carries a valid signature (base64 RSA / SHA-256) from the jt-ipam signing key. #>
    param([byte[]] $Bytes, [string] $SignatureB64)
    try {
        $rsa = New-Object Security.Cryptography.RSACryptoServiceProvider
        $kp = New-Object Security.Cryptography.RSAParameters
        $kp.Modulus = [Convert]::FromBase64String($AGENT_SIGNING_MODULUS)
        $kp.Exponent = [Convert]::FromBase64String($AGENT_SIGNING_EXPONENT)
        $rsa.ImportParameters($kp)
        return $rsa.VerifyData($Bytes, "SHA256", [Convert]::FromBase64String($SignatureB64.Trim()))
    } catch { return $false }
}

function Invoke-SelfUpdate {
    param([string] $ServerSha, [bool] $ForceUpdate)
    if (-not $ForceUpdate -and -not $AUTO_UPDATE) { return }
    if (-not $ServerSha) { return }
    $mine = Get-FileSha256 -Path $SELF
    if ($mine -eq $ServerSha) { return }
    Write-Log "[update] server has a newer agent, downloading update..."
    $tmp = "$SELF.new"
    try {
        $bytes = Invoke-Jt -Url "$API/agent.ps1"
        [IO.File]::WriteAllBytes($tmp, $bytes)
        if ((Get-FileSha256 -Path $tmp) -ne $ServerSha) {
            Write-Log "[update] downloaded sha mismatch, skipping this round"
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            return
        }
        # Only a copy signed with the jt-ipam signing key replaces this script (it runs as SYSTEM).
        $sig = Invoke-JtText -Url "$API/agent.ps1.sig"
        if (-not (Test-JtSignature -Bytes $bytes -SignatureB64 $sig)) {
            Write-Log "[update] SIGNATURE CHECK FAILED - the downloaded agent is not signed by jt-ipam; not installing it"
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
            return
        }
        Move-Item -LiteralPath $tmp -Destination $SELF -Force
        Write-Log "[update] updated, re-running new version"
        $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $SELF, "-Config", $Config)
        if ($DryRun) { $argv += "-DryRun" }
        if ($Force) { $argv += "-Force" }
        if ($DebugLog) { $argv += "-DebugLog" }
        & powershell.exe @argv
        exit $LASTEXITCODE
    } catch {
        Write-Log "[update] failed: $($_.Exception.Message)"
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

# ─────────────────── Main ───────────────────

if (-not (Test-Path -LiteralPath $STATE_DIR)) { $null = New-Item -ItemType Directory -Path $STATE_DIR -Force }

$check = $null
try {
    $check = Invoke-JtText -Url "$API/check?format=json" | ConvertFrom-Json
} catch {
    Write-Log "check failed (cannot reach $SERVER): $($_.Exception.Message)"
    exit 1
}

$serverSha = ""
if ($check.PSObject.Properties.Name -contains "agent_ps1_sha") { $serverSha = $check.agent_ps1_sha }
Invoke-SelfUpdate -ServerSha $serverSha -ForceUpdate ([bool] $Upgrade)
if ($Upgrade) {
    # If an update was available Invoke-SelfUpdate already re-ran and exited.
    Write-Log "[upgrade] agent is already at the latest version (v$AGENT_VERSION)"
    exit 0
}

$byName = @{}
foreach ($c in $check.certificates) { $byName[$c.cert] = $c }

$failed = 0
$n = 1
while ($true) {
    $cert = Get-Cfg $CFG "DEPLOY_${n}_CERT"
    if (-not $cert) { break }

    # Collect this deployment's fields into a plain hashtable keyed by the suffix
    $D = @{}
    $prefix = "DEPLOY_${n}_"
    foreach ($k in $CFG.Keys) {
        if ($k.StartsWith($prefix)) { $D[$k.Substring($prefix.Length)] = $CFG[$k] }
    }
    $prof = Get-Cfg $D "PROFILE" "iis"   # named $prof: $profile is an automatic variable
    $target = Get-DeploymentTarget -D $D -Profile $prof
    $n++

    if (-not $byName.ContainsKey($cert)) {
        Write-Log "[$cert/$prof] cert not found on server or not in scope, skipping"
        Add-Report $cert $prof "skipped" "" "" "not in scope / no current version"
        continue
    }
    $fp = $byName[$cert].fingerprint
    $na = $byName[$cert].not_after

    if (-not $DryRun -and -not $Force -and (Get-StateFingerprint -Cert $cert -Profile $prof -Target $target) -eq $fp) {
        # Already applied locally. Still report, so the server reflects this deployment even
        # when nothing changed (e.g. after re-keying the agent).
        Write-Log "[$cert/$prof] already up to date ($($fp.Substring(0, [Math]::Min(12, $fp.Length)))...), skipping"
        Add-Report $cert $prof "ok" $fp $na "already up to date"
        continue
    }

    $ok = $false
    try {
        switch ($prof) {
            "iis"   { $ok = Invoke-DeployIis   -D $D -Cert $cert -Fingerprint $fp -NotAfter $na }
            "winrm" { $ok = Invoke-DeployWinrm -D $D -Cert $cert -Fingerprint $fp -NotAfter $na }
            "rdp"   { $ok = Invoke-DeployRdp   -D $D -Cert $cert -Fingerprint $fp -NotAfter $na }
            "store" { $ok = Invoke-DeployStore -D $D -Cert $cert -Fingerprint $fp -NotAfter $na }
            "files" { $ok = Invoke-DeployFiles -D $D -Cert $cert -Fingerprint $fp -NotAfter $na }
            default {
                Write-Log "[$cert/$prof] unknown profile (use iis, winrm, rdp, store or files)"
                Add-Report $cert $prof "failed" $fp $na "unknown profile '$prof'"
            }
        }
    } catch {
        Write-Log "[$cert/$prof] error: $($_.Exception.Message)"
        Add-Report $cert $prof "failed" $fp $na $_.Exception.Message
    }

    if ($ok) {
        if (-not $DryRun) { Set-StateFingerprint -Cert $cert -Profile $prof -Target $target -Fingerprint $fp }
    } else {
        $failed = 1
    }
}

Send-Report
exit $failed
