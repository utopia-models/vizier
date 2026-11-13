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

fn main() {
    tauri::Builder::default()
        .plugin(talk_shortcuts())
        .setup(|app| {
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
                if keep.is_minimized().unwrap_or(false) {
                    let _ = keep.unminimize();
                }
                if !keep.is_visible().unwrap_or(true) {
                    let _ = keep.show();
                }
                if !keep.is_always_on_top().unwrap_or(false) {
                    let _ = keep.set_always_on_top(true);
                }
                place(&keep);
            });
            #[cfg(windows)]
            grant_media(&w);
            let pin = w.clone();
            w.on_window_event(move |e| match e {
                // Pinned: anything that moves or resizes it (a snap, a display change, a layout script) puts it back.
                WindowEvent::Moved(_) | WindowEvent::Resized(_) | WindowEvent::ScaleFactorChanged { .. } => place(&pin),
                _ => {}
            });
            if std::env::args().any(|a| a == "--selftest") {
                w.eval("window.addEventListener('load',()=>window.vizierSelftest&&window.vizierSelftest());setTimeout(()=>window.vizierSelftest&&window.vizierSelftest(),1500)")?;
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![selftest_report, config, snapshot])
        .run(tauri::generate_context!())
        .expect("error while running Vizier");
}

/// Alt+E toggles Ears and Alt+M toggles Mouth in ui/talk.js, from any app (global shortcuts). Each press flips one
/// switch; the page owns the state. With no talk config the page has no `vizierTalk.state.room`, and a press does nothing.
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
    tauri_plugin_global_shortcut::Builder::new()
        .with_shortcuts([ears, mouth, down, up, tab, ptab, pdown, pup, pleft, pright, lock, follow])
        .expect("Alt+E / Alt+M / Alt+Up / Alt+Down / Alt+T / Tab / Up / Down / Alt+K")
        .with_handler(move |app, sc, ev| {
            if ev.state() != ShortcutState::Pressed {
                return;
            }
            if sc == &follow {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.eval("window.vizierFollow&&window.vizierFollow()");
                }
                return;
            }
            if sc == &pleft || sc == &pright {
                if let Some(w) = app.get_webview_window("main") {
                    let d = if sc == &pright { 1 } else { -1 };
                    let _ = w.eval(&format!("window.vizierPart&&window.vizierPart({d})"));
                }
                return;
            }
            if sc == &lock {
                let gs = app.global_shortcut();
                if gs.is_registered(ptab) {
                    for k in [ptab, pdown, pup, pleft, pright] { let _ = gs.unregister(k); }
                } else {
                    for k in [ptab, pdown, pup, pleft, pright] { let _ = gs.register(k); }
                }
                return;
            }
            if sc == &tab || sc == &ptab {
                if let Some(w) = app.get_webview_window("main") {
                    let _ = w.eval("window.vizierTab&&window.vizierTab()");
                }
                return;
            }
            if sc == &down || sc == &up || sc == &pdown || sc == &pup {
                if let Some(w) = app.get_webview_window("main") {
                    let d = if sc == &down || sc == &pdown { 1 } else { -1 };
                    let _ = w.eval(&format!("window.vizierStep&&window.vizierStep({d})"));
                }
                return;
            }
            let k = if sc == &ears { "ears" } else if sc == &mouth { "mouth" } else { return };
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.eval(&format!("window.vizierTalk&&window.vizierTalk.state.room&&window.vizierTalk.toggle('{k}','hotkey')"));
            }
        })
        .build()
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
    if let Some(d) = std::env::current_exe().ok().and_then(|e| e.parent().map(|p| p.to_path_buf())) {
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
