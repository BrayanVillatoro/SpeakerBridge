# Sets up Speaker Bridge on this PC. It installs what is missing (Python, the
# VB-CABLE virtual audio driver, the Python packages), adds a firewall rule,
# makes Speaker Bridge start at login, and starts it.
# Safe to run again (for example after moving this folder).
$here = $PSScriptRoot
$script = Join-Path $here "speaker_bridge.py"
$cableZip = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack46.zip"
$cablePage = "https://vb-audio.com/Cable/"

function Find-Python {
    try {
        $p = & python -c "import sys; print(sys.executable)" 2>$null
        if ($p -and (Test-Path $p)) { return $p }
    } catch {}
    try {
        $p = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($p -and (Test-Path $p)) { return $p }
    } catch {}
    # installed but not on PATH (for example, installed a moment ago)
    Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" `
        -ErrorAction SilentlyContinue | Sort-Object FullName -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}

function Test-Cable {
    [bool](Get-CimInstance Win32_SoundDevice | Where-Object { $_.Name -like "*VB-Audio Virtual Cable*" })
}

Write-Host "== Speaker Bridge setup ==" -ForegroundColor Cyan

# 1. Python
$python = Find-Python
if (-not $python -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "Python is not installed. Installing it (this can take a minute)..."
    & winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    $python = Find-Python
}
if (-not $python) {
    Write-Host "Python could not be installed automatically." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
    Start-Process "https://www.python.org/downloads/"
    exit 1
}
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }
Write-Host "Python: ready"

# 2. Python packages
Write-Host "Installing Python packages..."
& $python -m pip install --quiet --disable-pip-version-check -r (Join-Path $here "requirements.txt")
if ($LASTEXITCODE -ne 0) { Write-Host "Package install failed (see above)." -ForegroundColor Red; exit 1 }

# 3. VB-CABLE: the donationware driver from VB-Audio that adds the extra sound output Windows sends audio to
$needsReboot = $false
if (Test-Cable) {
    Write-Host "Virtual sound output (VB-CABLE): ready"
} else {
    Write-Host "Installing the virtual sound output: VB-CABLE, made by VB-Audio (vb-audio.com)."
    Write-Host "VB-CABLE is donationware: free to use, and you can donate at $cablePage if you find it useful."
    Write-Host "Approve the Windows admin prompt, and click 'Install' if Windows asks about the driver."
    try {
        $tmp = Join-Path $env:TEMP "speaker_bridge_vbcable"
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory $tmp | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest $cableZip -OutFile "$tmp\vbcable.zip" -UseBasicParsing
        Expand-Archive "$tmp\vbcable.zip" -DestinationPath $tmp
        Start-Process "$tmp\VBCABLE_Setup_x64.exe" -ArgumentList "-i", "-h" -Verb RunAs -Wait
        Start-Sleep -Seconds 3
    } catch {
        Write-Host "  Automatic install did not work: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    if (Test-Cable) {
        Write-Host "Virtual sound output (VB-CABLE): installed"
        $needsReboot = $true
    } else {
        Write-Host "Virtual sound output (VB-CABLE): NOT INSTALLED" -ForegroundColor Yellow
        Write-Host "  Opening $cablePage - download it, unzip, right-click VBCABLE_Setup_x64.exe >"
        Write-Host "  'Run as administrator', click 'Install Driver', then restart the PC."
        Write-Host "  You do not need to run this setup again afterwards."
        Start-Process $cablePage
        $needsReboot = $true
    }
}

# 4. Firewall: the Sonos fetches the audio from this PC on TCP port 5757
if (Get-NetFirewallRule -DisplayName "Speaker Bridge" -ErrorAction SilentlyContinue) {
    Write-Host "Firewall rule: ready"
} else {
    Write-Host "Adding a firewall rule (approve the Windows admin prompt)..."
    try {
        Start-Process netsh -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList `
            'advfirewall firewall add rule name="Speaker Bridge" dir=in action=allow protocol=TCP localport=5757 remoteip=localsubnet'
    } catch {}
    if (Get-NetFirewallRule -DisplayName "Speaker Bridge" -ErrorAction SilentlyContinue) {
        Write-Host "Firewall rule: added"
    } else {
        Write-Host "Firewall rule: NOT added. The Sonos may not be able to reach this PC; run this again and approve the prompt." -ForegroundColor Yellow
    }
}

# 5. Start at login
$startup = [Environment]::GetFolderPath("Startup")
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startup "Speaker Bridge.lnk"))
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$script`""
$shortcut.WorkingDirectory = $here
$shortcut.Save()
Write-Host "Starts at login: yes"

# 6. (Re)start it now
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like "*speaker_bridge.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Process $pythonw -ArgumentList "`"$script`"" -WorkingDirectory $here

Write-Host ""
Write-Host "Done. Speaker Bridge is running (speaker icon in the system tray, near the clock)." -ForegroundColor Green
if ($needsReboot) {
    Write-Host "Restart the PC once to finish setting up the virtual sound output." -ForegroundColor Yellow
}
Write-Host "To play on the Sonos: click the speaker icon in the taskbar and choose"
Write-Host "'CABLE Input (VB-Audio Virtual Cable)' as the sound output."
Write-Host "Right-click the tray icon to choose the Sonos speaker and set its volume."
