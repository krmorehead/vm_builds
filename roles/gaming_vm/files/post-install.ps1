# Post-install script for Sunshine gaming VM image build.
# Runs during FirstLogonCommands via autounattend.xml.
# Installs: Sunshine, GZDoom + Freedoom, Windows Firewall rules.

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

$logFile = "C:\post-install.log"
function Log { param($msg); $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"; "$ts $msg" | Tee-Object -FilePath $logFile -Append }

Log "=== Post-install script starting ==="

Log "Defender and Windows Update already disabled via autounattend.xml"

# ── Wait for network ──────────────────────────────────────────────
Log "Waiting for network connectivity..."
$retries = 0
while ($retries -lt 60) {
    try {
        $null = Test-NetConnection -ComputerName "github.com" -Port 443 -InformationLevel Quiet -ErrorAction Stop
        if ($?) { break }
    } catch {}
    Start-Sleep -Seconds 5
    $retries++
}
if ($retries -ge 60) { Log "WARNING: Network not available after 5 minutes, continuing anyway" }

# ── Install Sunshine ──────────────────────────────────────────────
Log "Installing Sunshine..."
$sunshineDir = "C:\Program Files\Sunshine"
$sunshineInstaller = "$env:TEMP\sunshine-installer.exe"

try {
    $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/LizardByte/Sunshine/releases/latest" -UseBasicParsing
    $asset = $releases.assets | Where-Object { $_.name -match "sunshine.*windows.*installer.*\.exe$" -or $_.name -match "sunshine-windows-installer\.exe$" } | Select-Object -First 1

    if (-not $asset) {
        $asset = $releases.assets | Where-Object { $_.name -match "\.exe$" -and $_.name -match "sunshine" } | Select-Object -First 1
    }

    if ($asset) {
        Log "Downloading Sunshine from: $($asset.browser_download_url)"
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $sunshineInstaller -UseBasicParsing
        Log "Running Sunshine installer..."
        Start-Process -FilePath $sunshineInstaller -ArgumentList "/S" -Wait -NoNewWindow
        Log "Sunshine installed successfully"
    } else {
        Log "WARNING: Could not find Sunshine installer in latest release"
    }
} catch {
    Log "WARNING: Failed to install Sunshine: $_"
}

# ── Install GZDoom + Freedoom ─────────────────────────────────────
Log "Installing GZDoom + Freedoom..."
$gzdoomDir = "C:\Games\GZDoom"
New-Item -ItemType Directory -Force -Path $gzdoomDir | Out-Null

try {
    $gzdoomReleases = Invoke-RestMethod -Uri "https://api.github.com/repos/ZDoom/gzdoom/releases/latest" -UseBasicParsing
    $gzdoomAsset = $gzdoomReleases.assets | Where-Object { $_.name -match "gzdoom.*windows\.zip$" } | Select-Object -First 1

    if ($gzdoomAsset) {
        $gzdoomZip = "$env:TEMP\gzdoom.zip"
        Log "Downloading GZDoom from: $($gzdoomAsset.browser_download_url)"
        Invoke-WebRequest -Uri $gzdoomAsset.browser_download_url -OutFile $gzdoomZip -UseBasicParsing
        Expand-Archive -Path $gzdoomZip -DestinationPath $gzdoomDir -Force
        Remove-Item $gzdoomZip -Force
        Log "GZDoom extracted to $gzdoomDir"
    } else {
        Log "WARNING: Could not find GZDoom Windows x64 zip in latest release"
    }
} catch {
    Log "WARNING: Failed to install GZDoom: $_"
}

try {
    $freedoomReleases = Invoke-RestMethod -Uri "https://api.github.com/repos/freedoom/freedoom/releases/latest" -UseBasicParsing
    $freedoomAsset = $freedoomReleases.assets | Where-Object { $_.name -match "freedoom.*\.zip$" } | Select-Object -First 1

    if ($freedoomAsset) {
        $freedoomZip = "$env:TEMP\freedoom.zip"
        Log "Downloading Freedoom from: $($freedoomAsset.browser_download_url)"
        Invoke-WebRequest -Uri $freedoomAsset.browser_download_url -OutFile $freedoomZip -UseBasicParsing
        Expand-Archive -Path $freedoomZip -DestinationPath "$env:TEMP\freedoom-extract" -Force
        Get-ChildItem -Path "$env:TEMP\freedoom-extract" -Filter "*.wad" -Recurse | Copy-Item -Destination $gzdoomDir -Force
        Remove-Item $freedoomZip -Force
        Remove-Item "$env:TEMP\freedoom-extract" -Recurse -Force
        Log "Freedoom WADs copied to $gzdoomDir"
    } else {
        Log "WARNING: Could not find Freedoom zip in latest release"
    }
} catch {
    Log "WARNING: Failed to install Freedoom: $_"
}

# ── Windows Firewall rules for Sunshine ───────────────────────────
Log "Configuring Windows Firewall for Sunshine..."
$sunshineRules = @(
    @{ Name = "Sunshine-WebUI"; Port = "47990"; Protocol = "TCP" },
    @{ Name = "Sunshine-RTSP";  Port = "47989"; Protocol = "TCP" },
    @{ Name = "Sunshine-Video"; Port = "47998"; Protocol = "UDP" },
    @{ Name = "Sunshine-Control"; Port = "47999"; Protocol = "UDP" },
    @{ Name = "Sunshine-Audio"; Port = "48000"; Protocol = "UDP" },
    @{ Name = "Sunshine-Mic";   Port = "48002"; Protocol = "UDP" }
)

foreach ($rule in $sunshineRules) {
    try {
        New-NetFirewallRule -DisplayName $rule.Name -Direction Inbound -Action Allow `
            -Protocol $rule.Protocol -LocalPort $rule.Port -ErrorAction Stop | Out-Null
        Log "Firewall rule added: $($rule.Name) ($($rule.Protocol)/$($rule.Port))"
    } catch {
        Log "WARNING: Failed to add firewall rule $($rule.Name): $_"
    }
}

# ── Enable RDP ────────────────────────────────────────────────────
Log "Enabling Remote Desktop..."
try {
    Set-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name "fDenyTSConnections" -Value 0 -Force
    Enable-NetFirewallRule -DisplayGroup "Remote Desktop" -ErrorAction SilentlyContinue
    Log "RDP enabled"
} catch {
    Log "WARNING: Failed to enable RDP: $_"
}

# ── Re-enable Defender and Windows Update ──────────────────────────
try {
    Set-MpPreference -DisableRealtimeMonitoring $false -ErrorAction SilentlyContinue
    Log "Defender real-time monitoring re-enabled"
} catch {
    Log "WARNING: Could not re-enable Defender: $_"
}
try {
    Set-Service wuauserv -StartupType Manual -ErrorAction SilentlyContinue
    Log "Windows Update service re-enabled"
} catch {
    Log "WARNING: Could not re-enable Windows Update: $_"
}

# ── Signal completion ─────────────────────────────────────────────
Log "=== Post-install script completed ==="
"COMPLETE" | Out-File -FilePath "C:\post-install-done.txt" -Encoding ASCII
