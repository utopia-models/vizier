# vizier-pin.ps1 (run every minute from a scheduled task): keep the Vizier window bottom-left and topmost, even when
# it was hidden, minimized or pushed off screen, and start the vizier-app task (launch.ps1) if vizier.exe is not
# running. Finds the window through the vizier.exe process. No other window is touched.
#   -X/-Y/-W/-H  the rectangle to hold (default: the bottom-left quarter of a 2560x1440 display, above the taskbar)
param([int]$X = 0, [int]$Y = 720, [int]$W = 1280, [int]$H = 672, [string]$Log = "C:\Users\Public\vizier-pin.log")
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public class VP { public delegate bool EP(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EP f, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int c);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int w, int hh, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  public struct RECT { public int L, T, R, B; }
  public static IntPtr ForPid(uint pid){ IntPtr f=IntPtr.Zero; EnumWindows((h,l)=>{ uint p; GetWindowThreadProcessId(h,out p);
      if(p==pid){ var s=new StringBuilder(256); GetWindowText(h,s,256); if(s.ToString()=="Utopia Vizier"){ f=h; return false; } } return true; }, IntPtr.Zero); return f; } }
"@
$log = @()
# Boot: vizier-app has no trigger of its own, so this minute task starts it.
if (-not (Get-Process -Name vizier -ErrorAction SilentlyContinue)) { Start-ScheduledTask vizier-app; Start-Sleep 8; $log += "started vizier-app" }
foreach ($p in (Get-Process -Name vizier -ErrorAction SilentlyContinue)) {
  $h = [VP]::ForPid([uint32]$p.Id)
  if ($h -eq [IntPtr]::Zero) { $log += "pid $($p.Id): no 'Utopia Vizier' window"; continue }
  $r = New-Object VP+RECT; [VP]::GetWindowRect($h, [ref]$r) | Out-Null
  [VP]::ShowWindow($h, 5) | Out-Null; [VP]::ShowWindow($h, 9) | Out-Null
  [VP]::SetWindowPos($h, [IntPtr](-1), $X, $Y, $W, $H, 0x0040) | Out-Null
  $r2 = New-Object VP+RECT; [VP]::GetWindowRect($h, [ref]$r2) | Out-Null
  $log += "pid $($p.Id) hwnd $($h): was $($r.L),$($r.T)-$($r.R),$($r.B) now $($r2.L),$($r2.T)-$($r2.R),$($r2.B)"
}
if (-not $log) { $log = @("vizier.exe not running") }
($log + "at $(Get-Date -Format o)") | Set-Content $Log
