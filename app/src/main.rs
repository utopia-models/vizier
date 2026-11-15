// Vizier desktop app. One always-on-top window pinned to the bottom-left quarter of the primary display (clipped
// to the work area, so above the taskbar). The UI (ui/index.html) talks to the Vizier server itself; this file only
// places the window, keeps it there, hides it from every capture, grants camera+mic, and hands the UI its
// configuration.
//
// Hidden from capture ALWAYS (a prompter and call notes must never reach a livestream, a screen share or a call):
// SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE) through Tauri's content protection, set in tauri.conf.json, again
// at setup, and checked every 2 s with the slot, topmost and un-minimize (a window found running but off screen).
// There is no switch to turn it off.
//
// The 2 s check READS first and writes only what drifted (#12581, "the cursor is very slow on the Vizier app"): it
// used to call SetWindowDisplayAffinity and SetWindowPos(HWND_TOPMOST) unconditionally every tick, each one a
// synchronous round trip onto the UI thread plus a DWM recomposition of a capture-excluded window, the thread that
// also answers the cursor's hit-testing (WM_NCHITTEST / WM_SETCURSOR) over this window.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{Manager, PhysicalPosition, PhysicalSize, WebviewWindow, WindowEvent};

mod park;

/// Bottom-left quarter of the primary monitor, clipped to its work area (so it never sits under the taskbar).
fn slot(w: &WebviewWindow) -> Option<(PhysicalPosition<i32>, PhysicalSize<u32>)> {
    let m = w.primary_monitor().ok().flatten()?;
    let (pos, size, wa) = (*m.position(), *m.size(), *m.work_area());
    let top = pos.y + (size.height / 2) as i32;
    let bottom = (wa.position.y + wa.size.height as i32).min(pos.y + size.height as i32);
    let h = (bottom - top).max(240) as u32;
    Some((PhysicalPosition::new(pos.x, top), PhysicalSize::new(size.width / 2, h)))
}

fn place(w: &WebviewWindow) {
    if let Some((p, s)) = slot(w) {
        if w.inner_size().ok() != Some(s) {
            let _ = w.set_size(s);
        }
        if w.outer_position().ok() != Some(p) {
            let _ = w.set_position(p);
        }
    }
}

/// `vizier.exe --smoke [--smoke-port=N] [--config=<file>]`: the app as the smoke test drives it (scripts/smoke/smoke.py).
/// WebView2 opens a DevTools port (default 9339) so the harness drives the real page with real key and wheel events, in a
/// profile of its own (%PUBLIC%\\vizier\\smoke\\webview), and no global shortcut is registered (the user's running Vizier
/// holds them; the harness presses Alt+V / Alt+R / Alt+E / Alt+M through `smoke_key`, the same code the shortcuts run).
fn smoke() -> bool {
    std::env::args().any(|a| a == "--smoke")
}
fn arg(name: &str) -> Option<String> {
    std::env::args().find_map(|a| a.strip_prefix(&format!("--{name}=")).map(str::to_string))
}

fn main() {
    if smoke() {
        let port = arg("smoke-port").unwrap_or_else(|| "9339".into());
        // The env var replaces the window's additionalBrowserArgs, so it carries them too (tauri.conf.json is the source).
        let conf: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap_or_default();
        let base = conf["app"]["windows"][0]["additionalBrowserArgs"].as_str().unwrap_or("").to_string();
        std::env::set_var("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", format!("{base} --remote-debugging-port={port}"));
        let dir = std::env::var("PUBLIC").map(|p| std::path::PathBuf::from(p).join("vizier").join("smoke").join("webview"))
            .unwrap_or_else(|_| std::env::temp_dir().join("vizier-smoke-webview"));
        std::env::set_var("WEBVIEW2_USER_DATA_FOLDER", dir);
    }
    let mut b = tauri::Builder::default();
    if !smoke() {
        b = b.plugin(talk_shortcuts());
    }
    b.setup(|app| {
            let w = app.get_webview_window("main").expect("main window");
            place(&w);
            w.set_content_protected(true)?;
            w.set_always_on_top(true)?;
            w.show()?;
            let keep = w.clone();
            std::thread::spawn(move || loop {
                std::thread::sleep(std::time::Duration::from_secs(2));
                if affinity(&keep) != "0x11" {
                    let _ = keep.set_content_protected(true);
                }
                // Alt+V parked it (park.rs): the reader hid it on purpose, so leave it minimized.
                let minimized = keep.is_minimized().unwrap_or(false);
                match park::keep(park::parked_for(std::time::Instant::now()), minimized) {
                    park::Keep::Leave => continue,
                    park::Keep::Unpark => park::set_parked(None),
                    park::Keep::Restore => {}
                }
                if minimized {
                    let _ = keep.unminimize();
                }
                if !keep.is_visible().unwrap_or(true) {
                    let _ = keep.show();
                }
                if !keep.is_always_on_top().unwrap_or(false) {
                    let _ = keep.set_always_on_top(true);
                }
                place(&keep);
                #[cfg(windows)]
                keep_rendering(&keep);
            });
            #[cfg(windows)]
            grant_media(&w);
            let pin = w.clone();
            w.on_window_event(move |e| match e {
                // Pinned: anything that moves or resizes it (a snap, a display change, a layout script) puts it back.
                // A minimize is a move too (Windows parks the window at -32000): leave a minimized window alone.
                WindowEvent::Moved(_) | WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. } => {
                    if !pin.is_minimized().unwrap_or(false) {
                        place(&pin)
                    }
                }
                _ => {}
            });
            if std::env::args().any(|a| a == "--selftest") {
                w.eval("window.addEventListener('load',()=>window.vizierSelftest&&window.vizierSelftest());setTimeout(()=>window.vizierSelftest&&window.vizierSelftest(),1500)")?;
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![selftest_report, config, snapshot, smoke_key])
        .run(tauri::generate_context!())
        .expect("error while running Vizier");
}

/// Alt+E toggles Ears and Alt+M toggles Mouth in ui/talk.js, from any app (global shortcuts). Each press flips one
/// switch; the page owns the state. With no talk config the page has no `vizierTalk.state.room`, and a press does nothing.
/// Alt+V (V for Vizier) pops the window up bottom-left, in its slot, and the next Alt+V hides it again (park.rs;
/// the deployment's issue 13798, the user's card 2026-11-15 2:09 PM: "Alt+V pops Vizier bottom-left, small"). Was Alt+V Alt+V.
fn talk_shortcuts() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
    let ears = Shortcut::new(Some(Modifiers::ALT), Code::KeyE);
    let mouth = Shortcut::new(Some(Modifiers::ALT), Code::KeyM);
    // Prompter lines from any app (a remote-desktop client in front swallows plain arrows): Alt+Down next line, Alt+Up previous.
    let down = Shortcut::new(Some(Modifiers::ALT), Code::ArrowDown);
    let up = Shortcut::new(Some(Modifiers::ALT), Code::ArrowUp);
    let tab = Shortcut::new(Some(Modifiers::ALT), Code::KeyT);   // Alt+T: next tab (SCRIPT / CONTEXT / DO NOT SAY)
    // Plain Tab / Up / Down, global (the reader never has to click the Vizier window to give it focus). They steal
    // those keys from every app on this machine, which is the point on a presenting laptop (the reader talks; an agent drives).
    // Alt+K releases them (and takes them again): the escape if the laptop is ever used for typing.
    let ptab = Shortcut::new(None, Code::Tab);
    let pdown = Shortcut::new(None, Code::ArrowDown);
    let pup = Shortcut::new(None, Code::ArrowUp);
    let pleft = Shortcut::new(None, Code::ArrowLeft);   // plain Left / Right: previous / next part, global like Up / Down
    let pright = Shortcut::new(None, Code::ArrowRight);
    let lock = Shortcut::new(Some(Modifiers::ALT), Code::KeyK);
    let follow = Shortcut::new(Some(Modifiers::ALT), Code::KeyF);   // Alt+F: the agent following the reader's voice on / off
    let vizier = Shortcut::new(Some(Modifiers::ALT), Code::KeyV);   // Alt+V: pop Vizier up / hide it
    let rec = Shortcut::new(Some(Modifiers::ALT), Code::KeyR);      // Alt+R: start / stop a VSL take (rec_toggle)
    tauri_plugin_global_shortcut::Builder::new()
        .with_shortcuts([ears, mouth, down, up, tab, ptab, pdown, pup, pleft, pright, lock, follow, vizier, rec])
        .expect("Alt+E / Alt+M / Alt+Up / Alt+Down / Alt+T / Tab / Up / Down / Alt+K / Alt+F / Alt+V / Alt+R")
        .with_handler(move |app, sc, ev| {
            if ev.state() != ShortcutState::Pressed {
                return;
            }
            let name = if sc == &vizier { "alt+v" } else if sc == &rec { "alt+r" } else if sc == &follow { "alt+f" }
                else if sc == &pleft { "left" } else if sc == &pright { "right" } else if sc == &tab || sc == &ptab { "tab" }
                else if sc == &down || sc == &pdown { "down" } else if sc == &up || sc == &pup { "up" }
                else if sc == &ears { "alt+e" } else if sc == &mouth { "alt+m" } else { "" };
            if sc == &lock {
                let gs = app.global_shortcut();
                if gs.is_registered(ptab) {
                    for k in [ptab, pdown, pup, pleft, pright] { let _ = gs.unregister(k); }
                } else {
                    for k in [ptab, pdown, pup, pleft, pright] { let _ = gs.register(k); }
                }
                return;
            }
            hotkey(app, name);
        })
        .build()
}

/// What a global shortcut does, by name: the one place both the shortcuts and the smoke test (`smoke_key`) run.
fn hotkey(app: &tauri::AppHandle, name: &str) {
    let Some(w) = app.get_webview_window("main") else { return };
    match name {
        "alt+v" => {
            let now = std::time::Instant::now();
            let parked = park::parked_for(now).is_some();
            match park::toggle(parked, w.is_minimized().unwrap_or(false)) {
                park::Toggle::Minimize => {
                    park::set_parked(Some(now));
                    let _ = w.minimize();
                }
                park::Toggle::Restore => {
                    park::set_parked(None);
                    let _ = w.unminimize();
                    let _ = w.show();
                    let _ = w.set_always_on_top(true);
                    place(&w);
                    let _ = w.set_focus();
                }
            }
        }
        "alt+r" => rec_toggle(app),
        "alt+f" => { let _ = w.eval("window.vizierFollow&&window.vizierFollow()"); }
        "left" | "right" => { let _ = w.eval(&format!("window.vizierPart&&window.vizierPart({})", if name == "right" { 1 } else { -1 })); }
        "tab" => { let _ = w.eval("window.vizierTab&&window.vizierTab()"); }
        "down" | "up" => { let _ = w.eval(&format!("window.vizierStep&&window.vizierStep({})", if name == "down" { 1 } else { -1 })); }
        "alt+e" | "alt+m" => {
            let k = if name == "alt+e" { "ears" } else { "mouth" };
            let _ = w.eval(&format!("window.vizierTalk&&window.vizierTalk.state.room&&window.vizierTalk.toggle('{k}','hotkey')"));
        }
        _ => {}
    }
}

/// The smoke test presses a global shortcut (only in `--smoke`; a normal launch refuses): -> is the window minimized after.
#[tauri::command]
fn smoke_key(app: tauri::AppHandle, w: WebviewWindow, key: String) -> Result<bool, String> {
    if !smoke() {
        return Err("smoke_key works only in --smoke".into());
    }
    hotkey(&app, &key);
    std::thread::sleep(std::time::Duration::from_millis(250));
    Ok(w.is_minimized().unwrap_or(false))
}

/// Alt+R: a VSL take on / off, native end to end. The page shows the asked-for state at once (vizierRecLocal), and this
/// process POSTs /vsl {"toggle": true} to the server itself on a thread of its own, so the press never waits on the
/// WebView's own timers or connections; the server answers at once (the take's start/stop runs there in the
/// background) and the page gets that answer (vizierRecServer). The SETTINGS "Recording" row and the agent's "record"
/// command POST the same /vsl, and the page's 1 s /health read keeps every one of them showing the same state.
fn rec_toggle(app: &tauri::AppHandle) {
    let w = app.get_webview_window("main");
    if let Some(w) = &w {
        let _ = w.eval("window.vizierRecLocal&&window.vizierRecLocal()");
    }
    let server = config(app.clone()).get("server").and_then(|v| v.as_str()).unwrap_or("http://127.0.0.1:8786").to_string();
    std::thread::spawn(move || {
        let answer = http_post_json(&server, "/vsl", r#"{"toggle":true,"src":"hotkey"}"#)
            .unwrap_or_else(|e| serde_json::json!({ "error": e }).to_string());
        if let Some(w) = w {
            let v: serde_json::Value = serde_json::from_str(&answer).unwrap_or_else(|_| serde_json::json!({ "error": "bad answer" }));
            let _ = w.eval(&format!("window.vizierRecServer&&window.vizierRecServer({v})"));
        }
    });
}

/// POST a JSON body to an http:// server (the Vizier server is plain HTTP on loopback): the body of the answer, or why
/// not. std only, so Alt+R adds no crate; 3 s at most.
fn http_post_json(base: &str, path: &str, body: &str) -> Result<String, String> {
    use std::io::{Read, Write};
    let rest = base.trim_end_matches('/').strip_prefix("http://").ok_or("Alt+R needs an http:// server")?;
    let (hostport, prefix) = rest.split_once('/').map(|(h, p)| (h, format!("/{p}"))).unwrap_or((rest, String::new()));
    let addr = if hostport.contains(':') { hostport.to_string() } else { format!("{hostport}:80") };
    let sock = std::net::ToSocketAddrs::to_socket_addrs(&addr).map_err(|e| e.to_string())?.next().ok_or("no address")?;
    let t = std::time::Duration::from_secs(3);
    let mut c = std::net::TcpStream::connect_timeout(&sock, t).map_err(|e| e.to_string())?;
    let _ = c.set_read_timeout(Some(t));
    let _ = c.set_nodelay(true);
    write!(c, "POST {prefix}{path} HTTP/1.1\r\nHost: {hostport}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len())
        .map_err(|e| e.to_string())?;
    let mut out = String::new();
    c.read_to_string(&mut out).map_err(|e| e.to_string())?;
    let (head, rest) = out.split_once("\r\n\r\n").ok_or("no answer")?;
    let code = head.split_whitespace().nth(1).unwrap_or("");
    if code.starts_with('2') || code == "409" {
        Ok(rest.to_string())
    } else {
        Err(format!("HTTP {code}"))
    }
}

/// The page must keep running and painting while the window is on screen but not the foreground app (the reader is
/// in Moonlight or a call; the keys are global). If WebView2 was left marked invisible (IsVisible false: hidden, not
/// rendered, timers parked), a key's eval runs but nothing is drawn until some window change wakes it, which is what
/// made Tab, Alt+E and Alt+M "take a while to register" while Alt+V (a native window change) was instant. Read first,
/// write only on drift (#12581).
#[cfg(windows)]
fn keep_rendering(w: &WebviewWindow) {
    if w.is_minimized().unwrap_or(false) || !w.is_visible().unwrap_or(true) {
        return;
    }
    let _ = w.with_webview(|wv| unsafe {
        let c = wv.controller();
        let mut v = windows::core::BOOL(1);
        if c.IsVisible(&mut v).is_ok() && !v.as_bool() {
            let _ = c.SetIsVisible(true);
        }
    });
}

/// The UI's configuration, merged from (lowest first): `<app config dir>/config.json`, `vizier.json` beside the
/// executable, then the environment (VIZIER_SERVER, VIZIER_MEDIA, VIZIER_CAM_PATH, VIZIER_CALL_PATH).
/// Keys: server, media, camPath, callPath. Unknown keys pass through untouched.
#[tauri::command]
fn config(app: tauri::AppHandle) -> serde_json::Value {
    let mut out = serde_json::Map::new();
    let mut files = Vec::new();
    if let Ok(d) = app.path().app_config_dir() {
        files.push(d.join("config.json"));
    }
    if let Some(f) = arg("config") {
        files.push(std::path::PathBuf::from(f));   // `--config=<file>` (the smoke test): instead of vizier.json
    } else if let Some(d) = std::env::current_exe().ok().and_then(|e| e.parent().map(|p| p.to_path_buf())) {
        files.push(d.join("vizier.json"));
    }
    for f in files {
        if let Some(serde_json::Value::Object(m)) =
            std::fs::read_to_string(&f).ok().and_then(|t| serde_json::from_str(&t).ok())
        {
            out.extend(m);
        }
    }
    for (env, key) in [("VIZIER_SERVER", "server"), ("VIZIER_MEDIA", "media"), ("VIZIER_CAM_PATH", "camPath"), ("VIZIER_CALL_PATH", "callPath")] {
        if let Ok(v) = std::env::var(env) {
            if !v.is_empty() {
                out.insert(key.into(), v.into());
            }
        }
    }
    serde_json::Value::Object(out)
}

/// `vizier.exe --selftest`: the page drives the prompter keys (key -> painted frame latency) and opens the mic once
/// (proving the permission handler), and reports here, with the window's real display affinity read back from
/// Windows; written to %PUBLIC%\\vizier\\selftest.txt, plus selftest.png (what the page shows), so a check can read
/// both over ssh without touching the screen.
#[tauri::command]
fn selftest_report(w: WebviewWindow, text: String) -> Result<(), String> {
    let geo = format!("window {:?} {:?} on_top={:?} affinity={}", w.outer_position().ok(), w.outer_size().ok(),
                      w.is_always_on_top().ok(), affinity(&w));
    let dir = std::env::var("PUBLIC").map(|p| std::path::PathBuf::from(p).join("vizier")).unwrap_or_else(|_| std::env::temp_dir());
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    #[cfg(windows)]
    capture_preview(&w, dir.join("selftest.png"));
    std::fs::write(dir.join("selftest.txt"), format!("{text}\n{geo}\n")).map_err(|e| e.to_string())
}

/// `talk_log`: one line from ui/talk.js (every join attempt, join, failure, disconnect, and the feed link) appended to
/// %PUBLIC%\\vizier\\talk.log (smoke runs: smoke\\talk.log), so why the app is or is not in its LiveKit room reads over ssh
/// with no console. Kept under 1 MB: past that the file starts over.
#[tauri::command]
fn talk_log(line: String) -> Result<(), String> {
    use std::io::Write;
    let mut dir = std::env::var("PUBLIC").map(|p| std::path::PathBuf::from(p).join("vizier")).unwrap_or_else(|_| std::env::temp_dir());
    if smoke() {
        dir = dir.join("smoke");
    }
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let f = dir.join("talk.log");
    let fresh = std::fs::metadata(&f).map(|m| m.len() > 1_000_000).unwrap_or(false);
    let mut o = std::fs::OpenOptions::new().create(true).append(!fresh).write(true).truncate(fresh).open(&f).map_err(|e| e.to_string())?;
    writeln!(o, "{}", line.replace(['\r', '\n'], " ")).map_err(|e| e.to_string())
}

/// `snapshot`: what the prompter shows right now, to %PUBLIC%\\vizier\\live.png, so an agent can LOOK at it over ssh (the
/// window is excluded from every screen capture on purpose). The page calls it when POST /prompter/snap asks for one.
#[tauri::command]
fn snapshot(w: WebviewWindow) -> Result<(), String> {
    let dir = std::env::var("PUBLIC").map(|p| std::path::PathBuf::from(p).join("vizier")).unwrap_or_else(|_| std::env::temp_dir());
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    #[cfg(windows)]
    capture_preview(&w, dir.join("live.png"));
    Ok(())
}

/// What the page shows, as WebView2 renders it, to a PNG. The window is excluded from every screen and window capture
/// (WDA_EXCLUDEFROMCAPTURE: a screen grab and PrintWindow both come back black), so the proof picture is taken from
/// inside the app; the exclusion itself is never lifted.
#[cfg(windows)]
fn capture_preview(w: &WebviewWindow, path: std::path::PathBuf) {
    use webview2_com::CapturePreviewCompletedHandler;
    use webview2_com::Microsoft::Web::WebView2::Win32::*;
    use windows::Win32::System::Com::{IStream, STGM_CREATE, STGM_WRITE};
    use windows::Win32::UI::Shell::SHCreateStreamOnFileEx;
    let _ = w.with_webview(move |wv| unsafe {
        let Ok(core) = wv.controller().CoreWebView2() else { return };
        let name = windows::core::HSTRING::from(path.as_os_str());
        let Ok(stream) = SHCreateStreamOnFileEx(&name, STGM_CREATE.0 | STGM_WRITE.0, 0x80, true, None::<&IStream>) else { return };
        let keep = stream.clone(); // released (and the file flushed) when the capture completes
        let done = CapturePreviewCompletedHandler::create(Box::new(move |_| {
            drop(keep);
            Ok(())
        }));
        let _ = core.CapturePreview(COREWEBVIEW2_CAPTURE_PREVIEW_IMAGE_FORMAT_PNG, &stream, &done);
    });
}

/// The window's display affinity as Windows reports it: 0x11 = WDA_EXCLUDEFROMCAPTURE (hidden), 0 = none.
#[cfg(windows)]
fn affinity(w: &WebviewWindow) -> String {
    #[link(name = "user32")]
    extern "system" {
        fn GetWindowDisplayAffinity(hwnd: isize, aff: *mut u32) -> i32;
    }
    let Ok(h) = w.hwnd() else { return "no-hwnd".into() };
    let mut a = 0u32;
    let ok = unsafe { GetWindowDisplayAffinity(h.0 as isize, &mut a) };
    if ok == 0 { "error".into() } else { format!("{a:#x}") }
}
#[cfg(not(windows))]
fn affinity(_: &WebviewWindow) -> String {
    "n/a".into()
}

/// Camera + mic auto-allowed for this app's own page (the WHIP publish): WebView2 otherwise shows a permission
/// prompt per origin, which nobody is at the laptop to click.
#[cfg(windows)]
fn grant_media(w: &WebviewWindow) {
    use webview2_com::Microsoft::Web::WebView2::Win32::*;
    use webview2_com::PermissionRequestedEventHandler;
    let _ = w.with_webview(|wv| unsafe {
        let Ok(core) = wv.controller().CoreWebView2() else { return };
        let mut token = Default::default();
        let handler = PermissionRequestedEventHandler::create(Box::new(|_, args| {
            if let Some(a) = args {
                let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
                a.PermissionKind(&mut kind)?;
                if kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE || kind == COREWEBVIEW2_PERMISSION_KIND_CAMERA {
                    a.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
                }
            }
            Ok(())
        }));
        let _ = core.add_PermissionRequested(&handler, &mut token);
    });
}
