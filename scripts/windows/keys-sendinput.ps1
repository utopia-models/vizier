# keys-sendinput.ps1: prove his OWN keys reach Vizier while OBS is full screen, with synthetic SendInput
# keys (the same path a real keyboard takes through the global shortcuts). Run on ETH, with OBS in front and full screen,
# Vizier on the SPEAKER SCRIPT tab and a script loaded:
#   powershell -File keys-sendinput.ps1 [-Server http://127.0.0.1:8765]
# Steps, each one a PASS/FAIL line, exit 1 on any FAIL:
#   1. the foreground window covers its whole monitor (OBS full screen);
#   2. Down x2 moves the speaker-script line by +2 (read back from GET /prompter/position, which the tab reports on each step);
#   3. Up x1 moves it by -1;
#   4. Alt+E twice and Alt+M twice each add talk.log lines (ears / mouth toggled and toggled back).
# Down / Up are Vizier's global shortcuts (Alt+K releases them); Alt+E / Alt+M are the ears / mouth shortcuts.
param([string]$Server = "http://127.0.0.1:8765")
Add-Type @"
using System; using System.Runtime.InteropServices;
public class KS {
  [StructLayout(LayoutKind.Sequential)] public struct KI { public ushort vk; public ushort sc; public uint fl; public uint t; public IntPtr x; }
  [StructLayout(LayoutKind.Explicit, Size=40)] public struct IN { [FieldOffset(0)] public uint type; [FieldOffset(8)] public KI ki; }
  [DllImport("user32.dll", SetLastError=true)] static extern uint SendInput(uint n, IN[] i, int sz);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out R r);
  [DllImport("user32.dll")] static extern int GetSystemMetrics(int i);
  public struct R { public int L, T, Rt, B; }
  public static void Key(ushort vk, bool up) { var i = new IN[1]; i[0].type = 1; i[0].ki.vk = vk; i[0].ki.fl = (uint)((up ? 2 : 0) | (vk >= 0x21 && vk <= 0x28 ? 1 : 0)); SendInput(1, i, Marshal.SizeOf(typeof(IN))); }
  public static bool FgFullScreen() { R r; GetWindowRect(GetForegroundWindow(), out r);
    return r.L <= 0 && r.T <= 0 && (r.Rt - r.L) >= GetSystemMetrics(0) && (r.B - r.T) >= GetSystemMetrics(1); }
}
"@
$fail = 0
function Say($ok, $m) { if ($ok) { "PASS $m" } else { "FAIL $m"; $script:fail = 1 } }
function Tap($vk, [ushort[]]$mods = @()) { foreach ($m in $mods) { [KS]::Key($m, $false) }; [KS]::Key($vk, $false); [KS]::Key($vk, $true); foreach ($m in $mods) { [KS]::Key($m, $true) }; Start-Sleep -Milliseconds 700 }
function Line { try { (Invoke-RestMethod "$Server/prompter/position").line } catch { -1 } }
$talk = "$env:PUBLIC\vizier\talk.log"
function TalkN { if (Test-Path $talk) { (Get-Content $talk).Count } else { 0 } }
Say ([KS]::FgFullScreen()) "foreground window is full screen (OBS)"
$a = Line; Tap 0x28; Tap 0x28; $b = Line
Say (($a -ge 1) -and ($b -eq $a + 2)) "Down x2: line $a -> $b (want +2, with the speaker-script tab open)"
Tap 0x26; $c = Line
Say ($c -eq $b - 1) "Up: line $b -> $c (want -1)"
$n = TalkN; Tap 0x45 @(0x12); Tap 0x45 @(0x12)
Say ((TalkN) -gt $n) "Alt+E x2 reached the app (talk.log $n -> $(TalkN))"
$n = TalkN; Tap 0x4D @(0x12); Tap 0x4D @(0x12)
Say ((TalkN) -gt $n) "Alt+M x2 reached the app (talk.log $n -> $(TalkN))"
exit $fail
