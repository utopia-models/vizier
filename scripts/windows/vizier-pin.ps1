# vizier-pin.ps1 (task vizier-pin, every minute): keep the Utopia Vizier app RUNNING and topmost on ETH. Never where it
# is, how big it is, or whether it is minimized: those are the reader's (the reader, 2026-11-17 00:37Z: "when I do Alt+V to
# minimize Vizier, it comes back after 10 seconds"; 00:38Z: "I would like to be able to move Vizier around... stuck to
# the bottom left... and change the size of it"). Until then this task un-minimized the window and SetWindowPos'd it to
# the bottom-left slot every minute (vizier-pin.log 2026-11-16 17:42:44 local: "was -32000,-32000 now 0,570-960,1140").
#   - vizier.exe not running: start the vizier-app task (boot: vizier-app has no trigger of its own).
#   - the window is minimized (Alt+V, the taskbar, Win+D): left alone.
#   - the window is on screen: HWND_TOPMOST with NOMOVE|NOSIZE|NOACTIVATE, and its rect is remembered in
#     C:\Users\Public\vizier\window.json ({"x","y","w","h"}, physical px). default-view puts it back THERE and the app
#     (main.rs saved_rect) opens there; an app build older than that is moved there once, within 2 min of its launch, if
#     it sits in the app's launch slot (bottom-left, half the screen each way).
#   - the window meets no monitor at all (a display change): put back at the remembered rect, else the launch slot.
# Finds the window through the vizier.exe process (a title lookup missed it on 2026-11-10). No other window is touched.
#   - every run: capture-audit.ps1 (Vizier must be invisible to every capture); its PASS/FAIL line joins this run's line.
# Log: C:\Users\Public\vizier-pin.log, one line per run (timestamp, then each window), the last 160 runs kept.
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class VP { public delegate bool EP(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EP f, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int hh, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
  public struct RECT { public int L, T, R, B; }
  public static IntPtr ForPid(uint pid){ IntPtr f=IntPtr.Zero; EnumWindows((h,l)=>{ uint p; GetWindowThreadProcessId(h,out p);
      if(p==pid){ var s=new StringBuilder(256); GetWindowText(h,s,256); if(s.ToString()=="Utopia Vizier"){ f=h; return false; } } return true; }, IntPtr.Zero); return f; } }
"@
[VP]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null   # per-monitor v2: physical pixels, 1:1 with default-view and fast.py
Add-Type -AssemblyName System.Windows.Forms
$Mem = 'C:\Users\Public\vizier\window.json'
# The app's launch slot (main.rs slot()): bottom-left of the primary monitor, half its width and half its height, on the work area.
$scr = [System.Windows.Forms.Screen]::PrimaryScreen; $wa = $scr.WorkingArea
$sw = [int]($scr.Bounds.Width / 2); $sh = [int]($scr.Bounds.Height / 2); $sx = $scr.Bounds.Left; $sy = $wa.Bottom - $sh
function OnScreen($r) {   # at least 100x50 px of the rect on some monitor
  foreach ($s in [System.Windows.Forms.Screen]::AllScreens) { $b = $s.Bounds
    $ox = [Math]::Min($r.R, $b.Right) - [Math]::Max($r.L, $b.Left); $oy = [Math]::Min($r.B, $b.Bottom) - [Math]::Max($r.T, $b.Top)
    if ($ox -ge 100 -and $oy -ge 50) { return $true } }
  return $false }
function Remembered() {
  try { $j = Get-Content $Mem -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($j.w -ge 320 -and $j.h -ge 180) { $r = New-Object VP+RECT; $r.L = [int]$j.x; $r.T = [int]$j.y; $r.R = [int]$j.x + [int]$j.w; $r.B = [int]$j.y + [int]$j.h
      if (OnScreen $r) { return $r } } } catch {}
  return $null }
$log = @()
if (-not (Get-Process -Name vizier -ErrorAction SilentlyContinue)) { Start-ScheduledTask vizier-app; Start-Sleep 8; $log += "started vizier-app" }
foreach ($p in (Get-Process -Name vizier -ErrorAction SilentlyContinue)) {
  $h = [VP]::ForPid([uint32]$p.Id)
  if ($h -eq [IntPtr]::Zero) { $log += "pid $($p.Id): no 'Utopia Vizier' window"; continue }
  if ([VP]::IsIconic($h)) { $log += "pid $($p.Id): minimized, left alone"; continue }
  $r = New-Object VP+RECT; [VP]::GetWindowRect($h, [ref]$r) | Out-Null
  $mem = Remembered
  $age = ((Get-Date) - $p.StartTime).TotalSeconds
  $inSlot = ($r.L -eq $sx -and $r.T -eq $sy -and ($r.R - $r.L) -eq $sw -and ($r.B - $r.T) -eq $sh)
  $same = ($mem -and $mem.L -eq $r.L -and $mem.T -eq $r.T -and $mem.R -eq $r.R -and $mem.B -eq $r.B)
  if (-not (OnScreen $r)) {   # off every monitor: back where the reader had it, else the launch slot
    if ($mem) { [VP]::SetWindowPos($h, [IntPtr](-1), $mem.L, $mem.T, $mem.R - $mem.L, $mem.B - $mem.T, 0x0050) | Out-Null; $log += "pid $($p.Id): off screen at $($r.L),$($r.T): back at remembered $($mem.L),$($mem.T)" }
    else { [VP]::SetWindowPos($h, [IntPtr](-1), $sx, $sy, $sw, $sh, 0x0050) | Out-Null; $log += "pid $($p.Id): off screen at $($r.L),$($r.T): back in the launch slot" }
  } elseif ($mem -and $age -lt 120 -and $inSlot -and -not $same) {
    [VP]::SetWindowPos($h, [IntPtr](-1), $mem.L, $mem.T, $mem.R - $mem.L, $mem.B - $mem.T, 0x0050) | Out-Null   # TOPMOST, SHOWWINDOW|NOACTIVATE
    $log += "pid $($p.Id): fresh launch in the slot: moved to remembered $($mem.L),$($mem.T) $($mem.R - $mem.L)x$($mem.B - $mem.T)"
  } else {
    [VP]::SetWindowPos($h, [IntPtr](-1), 0, 0, 0, 0, 0x0013) | Out-Null   # TOPMOST, NOSIZE|NOMOVE|NOACTIVATE: where it is, as big as it is
    $txt = '{{"x":{0},"y":{1},"w":{2},"h":{3}}}' -f $r.L, $r.T, ($r.R - $r.L), ($r.B - $r.T)
    if (-not (Test-Path $Mem) -or (Get-Content $Mem -Raw -ErrorAction SilentlyContinue) -ne $txt) { [IO.File]::WriteAllText($Mem, $txt) }
    $log += "pid $($p.Id) hwnd $($h): topmost at $($r.L),$($r.T) $($r.R - $r.L)x$($r.B - $r.T) (remembered)"
  }
}
if (-not $log) { $log = @("vizier.exe not running") }
# Capture audit every run (knowledge#14720): a screen capture that shows Vizier is a FAIL in this log and in
# capture-audit.log beside the app (capture-audit.ps1, next to this file in the repo; deployed as C:\Users\Public\vizier\capture-audit.ps1).
$audit = 'C:\Users\Public\vizier\capture-audit.ps1'
if (Test-Path $audit) { $log += ((& powershell -NoProfile -ExecutionPolicy Bypass -File $audit 2>&1 | Select-Object -Last 1) -replace '^\S+ ', '') }
$LogFile = 'C:\Users\Public\vizier-pin.log'   # one line per run, the last 160 kept (about 2.7 h): the history a "why did it move" question needs
$hist = @(if (Test-Path $LogFile) { Get-Content $LogFile | Select-Object -Last 159 })
($hist + ("{0} {1}" -f (Get-Date -Format o), ($log -join ' | '))) | Set-Content $LogFile
