# launch.ps1: start (or restart) vizier.exe in the signed-in user's desktop session through a scheduled task, so it
# can be run over SSH (a process started from an SSH session has no desktop).
#   powershell -ExecutionPolicy Bypass -File launch.ps1 -User <windows user> [-Dir C:\Users\Public\vizier] [-Arg --selftest]
param([Parameter(Mandatory = $true)][string]$User, [string]$Dir = "C:\Users\Public\vizier", [string]$Arg = "")
$exe = Join-Path $Dir "vizier.exe"
Stop-Process -Name vizier -ErrorAction SilentlyContinue; Start-Sleep -Milliseconds 500
Remove-Item (Join-Path $Dir "selftest.txt") -ErrorAction SilentlyContinue
$a = if ($Arg) { New-ScheduledTaskAction -Execute $exe -Argument $Arg -WorkingDirectory $Dir } else { New-ScheduledTaskAction -Execute $exe -WorkingDirectory $Dir }
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
$p = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName vizier-app -Action $a -Settings $s -Principal $p -Force | Out-Null
Start-ScheduledTask -TaskName vizier-app
