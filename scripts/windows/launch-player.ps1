# launch-player.ps1: open the clip player full screen on this PC (knowledge#17355). No compile: the player is one HTML page
# the Vizier server serves at /player; it follows the reading cursor (GET /events) and plays clips/<slug>/row-<i>.webm.
#   powershell -ExecutionPolicy Bypass -File launch-player.ps1 -User <windows user> [-Slug <script slug>] [-Server http://127.0.0.1:18787]
# Runs in the signed-in user's desktop session through a scheduled task (a process started over SSH has no desktop).
# Its own Chrome profile (a kiosk window of its own, never the reader's Chrome), so nothing depends on his tabs or focus.
# The window is a normal capturable window; Vizier keeps WDA_EXCLUDEFROMCAPTURE. After the launch, vizier-pin.ps1 is run
# once so Vizier is topmost again and the arrow keys keep working while the player is up.
param([Parameter(Mandatory = $true)][string]$User, [string]$Slug = "current", [string]$Server = "http://127.0.0.1:18787",
      [string]$Chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe")
if (-not (Test-Path $Chrome)) { $Chrome = "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" }
if (-not (Test-Path $Chrome)) { Write-Error "chrome.exe not found"; exit 1 }
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | Where-Object { $_.CommandLine -like "*vizier-player-profile*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$prof = "C:\Users\Public\vizier-player-profile"
$url = "$Server/player?slug=$Slug"
$args = "--app=$url --kiosk --user-data-dir=$prof --autoplay-policy=no-user-gesture-required --no-first-run --disable-features=Translate --overscroll-history-navigation=0"
$a = New-ScheduledTaskAction -Execute $Chrome -Argument $args
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
$p = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName vizier-player -Action $a -Settings $s -Principal $p -Force | Out-Null
Start-ScheduledTask -TaskName vizier-player
Start-Sleep -Seconds 3
$pin = Join-Path $PSScriptRoot "vizier-pin.ps1"
if (Test-Path $pin) { & powershell -ExecutionPolicy Bypass -File $pin | Out-Null }   # Vizier back on top; the player never owned the keys
Write-Output "player up: $url"
