# capture-audit.ps1: is Vizier invisible to screen capture right now? (issue 14720, the user 2026-11-17: "Vizier is
# supposed to be invisible no matter what, only to me"). Run on ETH in the signed-in session (the vizier-pin task runs it
# every minute; by hand over ssh: powershell -ExecutionPolicy Bypass -File C:\Users\Public\vizier\capture-audit.ps1).
#
# Two legs, both must pass:
#   affinity  every visible top-level window of vizier.exe (>= 50x50 px) reads WDA_EXCLUDEFROMCAPTURE (0x11) from
#             GetWindowDisplayAffinity, the flag OBS display capture (DXGI and WGC), ffmpeg ddagrab, Chrome Remote Desktop
#             and every screenshot API honour.
#   pixels    a real screen capture (GDI, which honours the flag the same way DXGI does) of the main window's rect is
#             compared with what the app itself renders (POST /prompter/snap -> live.png, WebView2's own CapturePreview,
#             which the flag does not touch). A close match means the capture shows Vizier: FAIL. Skipped (not passed)
#             when the window is minimized or the app does not answer the snap within 6 s.
# Output: one line, "capture-audit PASS|FAIL|SKIP ...", appended to C:\Users\Public\vizier\capture-audit.log (last 500
# lines kept) and printed. Exit 0 PASS or SKIP, 1 FAIL. The capture is saved to capture-audit.png for a human to look at.
param([string]$Server = "", [int]$Threshold = 8)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing, System.Windows.Forms
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public class CA { public delegate bool EP(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EP f, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowDisplayAffinity(IntPtr h, out uint a);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
  public struct RECT { public int L, T, R, B; }
  public static List<IntPtr> ForPid(uint pid){ var o=new List<IntPtr>(); EnumWindows((h,l)=>{ uint p; GetWindowThreadProcessId(h,out p);
      if(p==pid && IsWindowVisible(h)) o.Add(h); return true; }, IntPtr.Zero); return o; } }
"@
[CA]::SetProcessDpiAwarenessContext([IntPtr](-4)) | Out-Null   # physical pixels, the same space as GetWindowRect
$dir = "C:\Users\Public\vizier"; $log = Join-Path $dir "capture-audit.log"
function Say($verdict, $why) {
  $line = "$(Get-Date -Format o) capture-audit $verdict $why"
  $keep = @(); if (Test-Path $log) { $keep = @(Get-Content $log -Tail 499) }
  Set-Content -Path $log -Value ($keep + $line) -Encoding UTF8
  Write-Output $line
  exit ($(if ($verdict -eq "FAIL") { 1 } else { 0 }))
}

$procs = @(Get-Process vizier -ErrorAction SilentlyContinue)
if (-not $procs) { Say "SKIP" "vizier.exe not running" }
$main = [IntPtr]::Zero; $bad = @(); $seen = 0
foreach ($p in $procs) {
  foreach ($h in [CA]::ForPid([uint32]$p.Id)) {
    $r = New-Object CA+RECT; [CA]::GetWindowRect($h, [ref]$r) | Out-Null
    if (($r.R - $r.L) -lt 50 -or ($r.B - $r.T) -lt 50) { continue }   # tao's 14x14 event-target window
    $a = [uint32]0; [CA]::GetWindowDisplayAffinity($h, [ref]$a) | Out-Null
    $t = New-Object System.Text.StringBuilder 256; [CA]::GetWindowText($h, $t, 256) | Out-Null
    $seen++
    if ($a -ne 0x11) { $bad += "hwnd $h '$t' affinity=0x$('{0:x}' -f $a)" }
    if ($t.ToString() -eq "Utopia Vizier") { $main = $h; $mr = $r }
  }
}
if ($bad) { Say "FAIL" ("affinity: " + ($bad -join "; ")) }
if ($main -eq [IntPtr]::Zero -or [CA]::IsIconic($main)) { Say "PASS" "affinity 0x11 on $seen window(s); pixels skipped (minimized)" }

# pixels: the app's own render of itself, fresh
if (-not $Server) {
  try { $Server = (Get-Content (Join-Path $dir "vizier.json") -Raw | ConvertFrom-Json).server } catch { }
  if (-not $Server) { $Server = "http://127.0.0.1:8786" }
}
$live = Join-Path $dir "live.png"
$before = if (Test-Path $live) { (Get-Item $live).LastWriteTimeUtc } else { [DateTime]::MinValue }
try { Invoke-RestMethod -Method Post -Uri "$($Server.TrimEnd('/'))/prompter/snap" -TimeoutSec 4 | Out-Null } catch { }
$deadline = (Get-Date).AddSeconds(6)
while ((Get-Date) -lt $deadline -and (-not (Test-Path $live) -or (Get-Item $live).LastWriteTimeUtc -le $before)) { Start-Sleep -Milliseconds 250 }
if (-not (Test-Path $live) -or (Get-Item $live).LastWriteTimeUtc -le $before) { Say "PASS" "affinity 0x11 on $seen window(s); pixels skipped (no fresh snap from $Server)" }
Start-Sleep -Milliseconds 300   # the PNG is flushed when CapturePreview completes

$w = $mr.R - $mr.L; $h = $mr.B - $mr.T
$shot = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($shot); $g.CopyFromScreen($mr.L, $mr.T, 0, 0, $shot.Size); $g.Dispose()
$shot.Save((Join-Path $dir "capture-audit.png"))
$fs = [System.IO.File]::OpenRead($live); $ref = [System.Drawing.Image]::FromStream($fs); $fs.Close()
function Small($img) {   # 48x27 grey thumbnail, the same for both, so a scale or DPI difference does not matter
  $b = New-Object System.Drawing.Bitmap 48, 27
  $gg = [System.Drawing.Graphics]::FromImage($b); $gg.InterpolationMode = "HighQualityBilinear"; $gg.DrawImage($img, 0, 0, 48, 27); $gg.Dispose()
  $v = New-Object double[] (48 * 27)
  for ($y = 0; $y -lt 27; $y++) { for ($x = 0; $x -lt 48; $x++) { $c = $b.GetPixel($x, $y); $v[$y * 48 + $x] = 0.299 * $c.R + 0.587 * $c.G + 0.114 * $c.B } }
  $b.Dispose(); return ,$v
}
$a1 = Small $shot; $a2 = Small $ref; $shot.Dispose(); $ref.Dispose()
$sum = 0.0; for ($i = 0; $i -lt $a1.Length; $i++) { $sum += [Math]::Abs($a1[$i] - $a2[$i]) }
$diff = [Math]::Round($sum / $a1.Length, 1)
if ($diff -lt $Threshold) { Say "FAIL" "pixels: the screen capture of Vizier's rect [$($mr.L),$($mr.T) ${w}x$h] matches the app's own render (mean diff $diff < $Threshold): the capture shows Vizier" }
Say "PASS" "affinity 0x11 on $seen window(s); pixels: capture of [$($mr.L),$($mr.T) ${w}x$h] differs from the app's render (mean diff $diff >= $Threshold)"
