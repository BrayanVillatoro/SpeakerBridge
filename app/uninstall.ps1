# Stops Speaker Bridge and removes its startup shortcut and firewall rule.
# Leaves Python, the Python packages and the VB-CABLE driver alone.
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like "*speaker_bridge.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

$link = Join-Path ([Environment]::GetFolderPath("Startup")) "Speaker Bridge.lnk"
if (Test-Path $link) { Remove-Item $link }

if (Get-NetFirewallRule -DisplayName "Speaker Bridge" -ErrorAction SilentlyContinue) {
    Write-Host "Removing the firewall rule (approve the Windows admin prompt)..."
    try {
        Start-Process netsh -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList `
            'advfirewall firewall delete rule name="Speaker Bridge"'
    } catch {}
}
Write-Host "Speaker Bridge is stopped and will no longer start at login. You can delete this folder."
