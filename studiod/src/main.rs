//! studiod: one small daemon in the recording user's session, beside OBS (the deployment's issue 15077).
//!
//! It owns four things and nothing else: ONE held obs-websocket v5 connection to OBS on loopback, the take record
//! (recording, take id, start time, output path), the cue position, and the fixed list of verbs that change them. Voice,
//! a hotkey and any CLI call the same verbs on 127.0.0.1:17900, so a start/stop costs one loopback round trip to OBS
//! (milliseconds) instead of crossing a tunnel, and the take's state lives where the recorder lives: a restart of any
//! server elsewhere cannot lose it.
//!
//! RECORD ONLY: the requests this client can send are ALLOWED below; a stream request is refused before a byte is sent,
//! and `rec start` refuses while OBS is streaming (a take is never laid over, or turned into, a public stream).
//!
//! HTTP on 127.0.0.1 (loopback only):
//!   GET  /status                 the state (also /health)
//!   POST /cmd {"id":"...","verb":"rec start","name":"my-take"}
//!        verbs: "rec start [name]" · "rec stop" · "rec toggle" · "status" · "cue next" · "cue back" · "cue goto <beat>"
//!               · "face orb" · "face cam"  (the hand-off: hide the face card source, show the orb source, or back;
//!                 in the current program scene; `--face-source` / `--orb-source`, default "Face" / "Orb")
//!        `id` makes a verb idempotent: the same id within 10 min returns the first answer and changes nothing.
//! State file: written on every change, write-then-rename (`--state`, default %PUBLIC%\vizier\studio-state.json).
//! Beacon: `--beacon http://host:port/path` POSTs the state every 2 s (plain http; a dead target only logs once).
//! OBS password: read from OBS's own obs-websocket config.json each connect (never copied), or `STUDIOD_OBS_PASSWORD`.
//! Windows: a GUI-subsystem exe (no console window ever opens on the user's screen); pass `--log <file>` to keep a log.
#![cfg_attr(windows, windows_subsystem = "windows")]
use base64::Engine;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

// Scene-item visibility (the face -> orb hand-off) is allowed too: it changes what is recorded, never starts a stream.
const ALLOWED: &[&str] = &["GetVersion", "GetRecordStatus", "GetStreamStatus", "StartRecord", "StopRecord",
                           "GetCurrentProgramScene", "GetSceneItemId", "SetSceneItemEnabled"];
const OUTPUTS: u64 = 1 << 6; // EventSubscription.Outputs: RecordStateChanged, StreamStateChanged
const RECONNECT_CAP_MS: u64 = 2000;

struct Cfg { port: u16, obs: String, obs_config: String, state: String, beacon: Option<String>, log: Option<String>, face_source: String, orb_source: String }

#[derive(Default)]
struct State {
    obs_up: bool, obs_error: String, obs_version: String,
    recording: bool, output_path: String, take_id: u64, name: String, started_at: f64,
    beat: i64, beat_name: String, face: String,
    rec_events: u64, last_rec_state: String, last_rec_event_at: Option<Instant>,
    last_cmd: Value, link_ok: Option<Instant>,
}

struct Req { rtype: String, data: Value, reply: Sender<Result<Value, String>> }

struct Shared {
    st: Mutex<State>, cv: Condvar, cfg: Cfg, tx: Mutex<Sender<Req>>,
    seen: Mutex<HashMap<String, (Instant, Value)>>, cmd_lock: Mutex<()>, t0: Instant,
}

fn now_s() -> f64 { SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0) }

fn log(sh: &Shared, msg: &str) {
    let line = format!("{:.3} {}\n", now_s(), msg);
    eprint!("{line}");
    if let Some(p) = &sh.cfg.log {
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(p) { let _ = f.write_all(line.as_bytes()); }
    }
}

// Full Jitter (AWS, "Exponential Backoff And Jitter"): a random point under min(cap, 100 ms * 2^n).
fn jitter_ms(attempt: u32) -> u64 {
    let ceil = (100u64 << attempt.min(10)).min(RECONNECT_CAP_MS);
    let r = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.subsec_nanos() as u64).unwrap_or(0);
    r % ceil.max(1)
}

fn auth_string(password: &str, salt: &str, challenge: &str) -> String {
    let b64 = base64::engine::general_purpose::STANDARD;
    let secret = b64.encode(Sha256::digest(format!("{password}{salt}").as_bytes()));
    b64.encode(Sha256::digest(format!("{secret}{challenge}").as_bytes()))
}

fn obs_password(cfg: &Cfg) -> String {
    if let Ok(p) = std::env::var("STUDIOD_OBS_PASSWORD") { return p; }
    std::fs::read_to_string(&cfg.obs_config).ok()
        .and_then(|s| serde_json::from_str::<Value>(&s).ok())
        .and_then(|v| v.get("server_password").and_then(|p| p.as_str()).map(String::from))
        .unwrap_or_default()
}

fn state_json(sh: &Shared, st: &State) -> Value {
    json!({
        "ok": true, "obs": {"up": st.obs_up, "error": st.obs_error, "version": st.obs_version},
        "recording": st.recording, "output_path": st.output_path, "take_id": st.take_id, "name": st.name,
        "started_at": if st.started_at > 0.0 { json!(st.started_at) } else { Value::Null },
        "beat": st.beat, "beat_name": st.beat_name, "face": st.face, "rec_state": st.last_rec_state,
        "uptime_s": sh.t0.elapsed().as_secs(), "last_cmd": st.last_cmd, "t": now_s(),
    })
}

fn save_state(sh: &Shared, st: &State) {
    let path = &sh.cfg.state;
    let tmp = format!("{path}.tmp");
    let body = state_json(sh, st).to_string();
    if std::fs::write(&tmp, body).and_then(|_| std::fs::rename(&tmp, path)).is_err() {
        eprintln!("state file {path} not written");
    }
}

// ---------- OBS: one held connection, reconnect forever with Full Jitter ----------
type Ws = tungstenite::WebSocket<TcpStream>;

fn obs_connect(sh: &Shared) -> Result<Ws, String> {
    let addr = sh.cfg.obs.trim_start_matches("ws://").trim_end_matches('/').to_string();
    let tcp = TcpStream::connect_timeout(&addr.parse().map_err(|e| format!("bad --obs {addr}: {e}"))?, Duration::from_secs(2))
        .map_err(|e| format!("connect {addr}: {e}"))?;
    tcp.set_nodelay(true).ok();
    tcp.set_read_timeout(Some(Duration::from_secs(3))).ok();
    let req = tungstenite::client::IntoClientRequest::into_client_request(format!("ws://{addr}"))
        .map_err(|e| e.to_string())?;
    let mut req = req;
    req.headers_mut().insert("Sec-WebSocket-Protocol", "obswebsocket.json".parse().unwrap());
    let (mut ws, _) = tungstenite::client(req, tcp).map_err(|e| format!("handshake: {e}"))?;
    let hello: Value = read_json(&mut ws)?;
    let mut ident = json!({"rpcVersion": 1, "eventSubscriptions": OUTPUTS});
    if let Some(a) = hello["d"].get("authentication") {
        let pw = obs_password(&sh.cfg);
        if pw.is_empty() { return Err(format!("OBS asks for a password and none is in {}", sh.cfg.obs_config)); }
        ident["authentication"] = json!(auth_string(&pw, a["salt"].as_str().unwrap_or(""), a["challenge"].as_str().unwrap_or("")));
    }
    ws.send(tungstenite::Message::text(json!({"op": 1, "d": ident}).to_string())).map_err(|e| e.to_string())?;
    let ok = read_json(&mut ws)?;
    if ok["op"] != 2 { return Err(format!("OBS refused identify: {ok}")); }
    let v = hello["d"]["obsWebSocketVersion"].as_str().unwrap_or("").to_string();
    sh.st.lock().unwrap().obs_version = v;
    ws.get_mut().set_read_timeout(Some(Duration::from_millis(20))).ok();
    Ok(ws)
}

fn read_json(ws: &mut Ws) -> Result<Value, String> {
    loop {
        match ws.read() {
            Ok(tungstenite::Message::Text(t)) => return serde_json::from_str(&t).map_err(|e| e.to_string()),
            Ok(tungstenite::Message::Close(c)) => return Err(format!("closed: {c:?}")),
            Ok(_) => continue,
            Err(e) => return Err(e.to_string()),
        }
    }
}

fn obs_loop(sh: Arc<Shared>, rx: Receiver<Req>) {
    let mut attempt = 0u32;
    let mut next_id = 1u64;
    loop {
        let mut ws = match obs_connect(&sh) {
            Ok(ws) => ws,
            Err(e) => {
                { let mut st = sh.st.lock().unwrap(); if st.obs_up || st.obs_error != e { log(&sh, &format!("obs down: {e}")); } st.obs_up = false; st.obs_error = e; }
                while let Ok(r) = rx.try_recv() { let _ = r.reply.send(Err("OBS is not connected".into())); }
                std::thread::sleep(Duration::from_millis(jitter_ms(attempt)));
                attempt += 1;
                continue;
            }
        };
        attempt = 0;
        {
            let mut st = sh.st.lock().unwrap();
            st.obs_up = true; st.obs_error.clear();
            log(&sh, &format!("obs up: {} (obs-websocket {})", sh.cfg.obs, st.obs_version));
        }
        let mut pending: HashMap<String, Sender<Result<Value, String>>> = HashMap::new();
        // Read OBS's own record status once, so a daemon (re)started mid-take knows the truth.
        let (stx, srx) = channel();
        let _ = sh.tx.lock().unwrap().send(Req { rtype: "GetRecordStatus".into(), data: json!({}), reply: stx });
        let sh2 = sh.clone();
        std::thread::spawn(move || if let Ok(Ok(d)) = srx.recv_timeout(Duration::from_secs(3)) {
            let mut st = sh2.st.lock().unwrap();
            st.recording = d["outputActive"].as_bool().unwrap_or(false);
            save_state(&sh2, &st); sh2.cv.notify_all();
        });
        let err = loop {
            while let Ok(r) = rx.try_recv() {
                if !ALLOWED.contains(&r.rtype.as_str()) { let _ = r.reply.send(Err(format!("{} is not allowed (record only)", r.rtype))); continue; }
                let id = format!("s{next_id}"); next_id += 1;
                let m = json!({"op": 6, "d": {"requestType": r.rtype, "requestId": id, "requestData": r.data}});
                match ws.send(tungstenite::Message::text(m.to_string())) {
                    Ok(_) => { pending.insert(id, r.reply); }
                    Err(e) => { let _ = r.reply.send(Err(e.to_string())); }
                }
            }
            match ws.read() {
                Ok(tungstenite::Message::Text(t)) => {
                    let m: Value = match serde_json::from_str(&t) { Ok(v) => v, Err(_) => continue };
                    let d = &m["d"];
                    if m["op"] == 7 {
                        if let Some(tx) = pending.remove(d["requestId"].as_str().unwrap_or("")) {
                            let ok = d["requestStatus"]["result"].as_bool().unwrap_or(false);
                            let _ = tx.send(if ok { Ok(d["responseData"].clone()) } else {
                                Err(format!("OBS {}: {} {}", d["requestType"].as_str().unwrap_or(""), d["requestStatus"]["code"], d["requestStatus"]["comment"].as_str().unwrap_or("")))
                            });
                        }
                    } else if m["op"] == 5 && d["eventType"] == "RecordStateChanged" {
                        let e = &d["eventData"];
                        let mut st = sh.st.lock().unwrap();
                        st.recording = e["outputActive"].as_bool().unwrap_or(false);
                        st.last_rec_state = e["outputState"].as_str().unwrap_or("").to_string();
                        if let Some(p) = e["outputPath"].as_str() { st.output_path = p.to_string(); }
                        st.rec_events += 1; st.last_rec_event_at = Some(Instant::now());
                        log(&sh, &format!("RecordStateChanged {} active={}", st.last_rec_state, st.recording));
                        save_state(&sh, &st);
                        sh.cv.notify_all();
                    }
                }
                Ok(tungstenite::Message::Close(c)) => break format!("OBS closed the websocket: {c:?}"),
                Ok(_) => {}
                Err(tungstenite::Error::Io(e)) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {
                    let _ = ws.flush();
                }
                Err(e) => break e.to_string(),
            }
        };
        for (_, tx) in pending.drain() { let _ = tx.send(Err("OBS websocket closed".into())); }
        let mut st = sh.st.lock().unwrap();
        st.obs_up = false; st.obs_error = err.clone();
        log(&sh, &format!("obs lost: {err}"));
        sh.cv.notify_all();
    }
}

fn obs(sh: &Shared, rtype: &str, data: Value) -> Result<Value, String> {
    let (tx, rx) = channel();
    sh.tx.lock().unwrap().send(Req { rtype: rtype.into(), data, reply: tx }).map_err(|e| e.to_string())?;
    rx.recv_timeout(Duration::from_secs(3)).map_err(|_| format!("OBS did not answer {rtype} in 3 s"))?
}

// Wait until RecordStateChanged reaches `want` (outputState) after event count `n0`. Returns ms waited.
fn wait_rec(sh: &Shared, n0: u64, want: &str, t0: Instant) -> Result<f64, String> {
    let mut st = sh.st.lock().unwrap();
    loop {
        if st.rec_events > n0 && st.last_rec_state == want {
            let at = st.last_rec_event_at.unwrap_or_else(Instant::now);
            return Ok(at.duration_since(t0).as_secs_f64() * 1000.0);
        }
        let left = Duration::from_secs(5).checked_sub(t0.elapsed()).ok_or(format!("no {want} from OBS within 5 s"))?;
        st = sh.cv.wait_timeout(st, left).unwrap().0;
    }
}

// ---------- verbs ----------
fn verb(sh: &Shared, b: &Value) -> (u16, Value) {
    let v = b["verb"].as_str().unwrap_or("").trim().to_string();
    let mut words = v.split_whitespace();
    let (w0, w1) = (words.next().unwrap_or(""), words.next().unwrap_or(""));
    let rest: Vec<&str> = words.collect();
    let _one = sh.cmd_lock.lock().unwrap(); // one verb at a time: two "rec start" can never start two takes
    let snapshot = |sh: &Shared| { let st = sh.st.lock().unwrap(); state_json(sh, &st) };
    match (w0, w1) {
        ("status", _) | ("", _) => (200, snapshot(sh)),
        ("rec", "toggle") => {
            let on = sh.st.lock().unwrap().recording;
            let mut b2 = b.clone(); b2["verb"] = json!(if on { "rec stop" } else { "rec start" });
            drop(_one);
            verb(sh, &b2)
        }
        ("rec", "start") => {
            if sh.st.lock().unwrap().recording { return (200, json!({"ok": true, "already": true, "state": snapshot(sh)})); }
            match obs(sh, "GetStreamStatus", json!({})) {
                Ok(s) if s["outputActive"].as_bool().unwrap_or(false) => return (409, json!({"ok": false, "error": "OBS is streaming: refused (record only)"})),
                Ok(_) => {}
                Err(e) => return (503, json!({"ok": false, "error": e})),
            }
            let name = b["name"].as_str().map(String::from).or_else(|| rest.first().map(|s| s.to_string())).unwrap_or_else(|| "take".into());
            let n0 = sh.st.lock().unwrap().rec_events;
            let t0 = Instant::now();
            if let Err(e) = obs(sh, "StartRecord", json!({})) { return (502, json!({"ok": false, "error": e})); }
            let ack_ms = t0.elapsed().as_secs_f64() * 1000.0;
            match wait_rec(sh, n0, "OBS_WEBSOCKET_OUTPUT_STARTED", t0) {
                Ok(ms) => {
                    let mut st = sh.st.lock().unwrap();
                    st.take_id += 1; st.name = name; st.started_at = now_s(); st.beat = 0; st.beat_name.clear();
                    save_state(sh, &st);
                    log(sh, &format!("rec start: take {} ack {ack_ms:.1} ms, RecordStateChanged {ms:.1} ms", st.take_id));
                    (200, json!({"ok": true, "ack_ms": ack_ms, "event_ms": ms, "state": state_json(sh, &st)}))
                }
                Err(e) => (504, json!({"ok": false, "error": e, "ack_ms": ack_ms})),
            }
        }
        ("rec", "stop") => {
            if !sh.st.lock().unwrap().recording { return (200, json!({"ok": true, "already": true, "state": snapshot(sh)})); }
            let n0 = sh.st.lock().unwrap().rec_events;
            let t0 = Instant::now();
            let r = match obs(sh, "StopRecord", json!({})) { Ok(r) => r, Err(e) => return (502, json!({"ok": false, "error": e})) };
            let ack_ms = t0.elapsed().as_secs_f64() * 1000.0;
            match wait_rec(sh, n0, "OBS_WEBSOCKET_OUTPUT_STOPPED", t0) {
                Ok(ms) => {
                    let mut st = sh.st.lock().unwrap();
                    if let Some(p) = r["outputPath"].as_str() { st.output_path = p.to_string(); }
                    save_state(sh, &st);
                    log(sh, &format!("rec stop: take {} -> {} ack {ack_ms:.1} ms, RecordStateChanged {ms:.1} ms", st.take_id, st.output_path));
                    (200, json!({"ok": true, "ack_ms": ack_ms, "event_ms": ms, "state": state_json(sh, &st)}))
                }
                Err(e) => (504, json!({"ok": false, "error": e, "ack_ms": ack_ms})),
            }
        }
        ("cue", d @ ("next" | "back" | "goto")) => {
            let mut st = sh.st.lock().unwrap();
            match d {
                "next" => st.beat += 1,
                "back" => st.beat = (st.beat - 1).max(0),
                _ => {
                    let target = rest.join(" ");
                    if target.is_empty() { return (400, json!({"ok": false, "error": "cue goto needs a beat"})); }
                    match target.parse::<i64>() { Ok(n) => { st.beat = n; st.beat_name.clear(); } Err(_) => st.beat_name = target }
                }
            }
            save_state(sh, &st);
            (200, json!({"ok": true, "state": state_json(sh, &st)}))
        }
        ("face", which @ ("orb" | "cam")) => {
            // the hand-off (Down into [AI voice]): face card off + orb on, in the scene being recorded; "cam" reverses it
            let scene = match obs(sh, "GetCurrentProgramScene", json!({})) {
                Ok(r) => r["currentProgramSceneName"].as_str().or(r["sceneName"].as_str()).unwrap_or("").to_string(),
                Err(e) => return (503, json!({"ok": false, "error": e})),
            };
            let orb = which == "orb";
            let mut set = Vec::new();
            let mut errs = Vec::new();
            for (src, on) in [(sh.cfg.face_source.clone(), !orb), (sh.cfg.orb_source.clone(), orb)] {
                let id = match obs(sh, "GetSceneItemId", json!({"sceneName": scene, "sourceName": src})) {
                    Ok(r) => r["sceneItemId"].as_i64(),
                    Err(e) => { errs.push(format!("{src}: {e}")); continue; }
                };
                let Some(id) = id else { errs.push(format!("{src}: not in scene {scene:?}")); continue };
                match obs(sh, "SetSceneItemEnabled", json!({"sceneName": scene, "sceneItemId": id, "sceneItemEnabled": on})) {
                    Ok(_) => set.push(json!({"source": src, "enabled": on})),
                    Err(e) => errs.push(format!("{src}: {e}")),
                }
            }
            let mut st = sh.st.lock().unwrap();
            if errs.is_empty() { st.face = which.into(); save_state(sh, &st); }
            log(sh, &format!("face {which} in {scene:?}: set {} errors {:?}", set.len(), errs));
            (if errs.is_empty() { 200 } else { 502 }, json!({"ok": errs.is_empty(), "scene": scene, "set": set, "errors": errs, "state": state_json(sh, &st)}))
        }
        _ => (400, json!({"ok": false, "error": format!("unknown verb {v:?}: rec start [name] | rec stop | rec toggle | status | cue next|back|goto <beat> | face orb|cam")})),
    }
}

fn cmd(sh: &Shared, b: Value) -> (u16, Value) {
    let id = b["id"].as_str().unwrap_or("").to_string();
    if !id.is_empty() {
        let mut seen = sh.seen.lock().unwrap();
        seen.retain(|_, (t, _)| t.elapsed() < Duration::from_secs(600));
        if let Some((_, r)) = seen.get(&id) { let mut r = r.clone(); r["replayed"] = json!(true); return (200, r); }
    }
    let (code, r) = verb(sh, &b);
    if !id.is_empty() && code < 500 { sh.seen.lock().unwrap().insert(id, (Instant::now(), r.clone())); }
    sh.st.lock().unwrap().last_cmd = json!({"verb": b["verb"], "code": code, "t": now_s()});
    (code, r)
}

// ---------- HTTP (loopback, one thread per connection, no keep-alive) ----------
fn http(sh: Arc<Shared>, mut s: TcpStream) {
    s.set_read_timeout(Some(Duration::from_secs(5))).ok();
    let mut rd = BufReader::new(match s.try_clone() { Ok(c) => c, Err(_) => return });
    let mut line = String::new();
    if rd.read_line(&mut line).is_err() { return; }
    let mut parts = line.split_whitespace();
    let (method, path) = (parts.next().unwrap_or("").to_string(), parts.next().unwrap_or("").to_string());
    let mut len = 0usize;
    loop {
        let mut h = String::new();
        if rd.read_line(&mut h).is_err() || h.trim().is_empty() { break; }
        if let Some((k, v)) = h.split_once(':') { if k.eq_ignore_ascii_case("content-length") { len = v.trim().parse().unwrap_or(0).min(65536); } }
    }
    let mut body = vec![0u8; len];
    if len > 0 && rd.read_exact(&mut body).is_err() { return; }
    let (code, out) = match (method.as_str(), path.split('?').next().unwrap_or("")) {
        ("GET", "/status") | ("GET", "/health") => { let st = sh.st.lock().unwrap(); (200, state_json(&sh, &st)) }
        ("POST", "/cmd") => match serde_json::from_slice::<Value>(&body) {
            Ok(b) => cmd(&sh, b),
            Err(e) => (400, json!({"ok": false, "error": format!("body is not JSON: {e}")})),
        },
        _ => (404, json!({"ok": false, "error": "GET /status · POST /cmd {\"id\",\"verb\",\"name\"}"})),
    };
    let b = out.to_string();
    let _ = write!(s, "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{b}", b.len());
}

fn beacon_loop(sh: Arc<Shared>) {
    let Some(url) = sh.cfg.beacon.clone() else { return };
    let rest = url.trim_start_matches("http://");
    let (hostport, path) = rest.split_once('/').map(|(h, p)| (h.to_string(), format!("/{p}"))).unwrap_or((rest.to_string(), "/".into()));
    let mut was_ok = true;
    loop {
        let body = { let st = sh.st.lock().unwrap(); state_json(&sh, &st).to_string() };
        let r = (|| -> std::io::Result<()> {
            let addr = std::net::ToSocketAddrs::to_socket_addrs(&hostport)?.next().ok_or(std::io::ErrorKind::NotFound)?;
            let mut s = TcpStream::connect_timeout(&addr, Duration::from_secs(1))?;
            s.set_read_timeout(Some(Duration::from_secs(2)))?;
            write!(s, "POST {path} HTTP/1.1\r\nHost: {hostport}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len())?;
            let mut head = [0u8; 12];
            s.read_exact(&mut head)?;
            if &head[9..10] == b"2" { Ok(()) } else { Err(std::io::Error::other(String::from_utf8_lossy(&head).to_string())) }
        })();
        match r {
            Ok(()) => { if !was_ok { log(&sh, &format!("beacon ok again: {url}")); } was_ok = true; sh.st.lock().unwrap().link_ok = Some(Instant::now()); }
            Err(e) => { if was_ok { log(&sh, &format!("beacon failed: {url}: {e}")); } was_ok = false; }
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

fn args() -> Cfg {
    let a: Vec<String> = std::env::args().collect();
    let get = |k: &str| a.iter().position(|x| x == k).and_then(|i| a.get(i + 1)).cloned();
    let appdata = std::env::var("APPDATA").unwrap_or_default();
    let public = std::env::var("PUBLIC").map(|p| format!("{p}\\vizier")).unwrap_or_else(|_| std::env::temp_dir().display().to_string());
    let sep = if cfg!(windows) { "\\" } else { "/" };
    Cfg {
        port: get("--port").and_then(|p| p.parse().ok()).unwrap_or(17900),
        obs: get("--obs").unwrap_or_else(|| "ws://127.0.0.1:4455".into()),
        obs_config: get("--obs-config").unwrap_or_else(|| format!("{appdata}\\obs-studio\\plugin_config\\obs-websocket\\config.json")),
        state: get("--state").unwrap_or_else(|| format!("{public}{sep}studio-state.json")),
        beacon: get("--beacon"),
        log: get("--log"),
        face_source: get("--face-source").unwrap_or_else(|| "Face".into()),
        orb_source: get("--orb-source").unwrap_or_else(|| "Orb".into()),
    }
}

fn main() {
    let cfg = args();
    let listener = match TcpListener::bind(("127.0.0.1", cfg.port)) {
        Ok(l) => l,
        Err(e) => { eprintln!("studiod: 127.0.0.1:{} is taken ({e}); another studiod runs", cfg.port); std::process::exit(3); }
    };
    let (tx, rx) = channel();
    let sh = Arc::new(Shared {
        st: Mutex::new(State::default()), cv: Condvar::new(), cfg, tx: Mutex::new(tx),
        seen: Mutex::new(HashMap::new()), cmd_lock: Mutex::new(()), t0: Instant::now(),
    });
    // Resume the take record from the state file (a daemon restart mid-take keeps take id, name, start time).
    if let Some(v) = std::fs::read_to_string(&sh.cfg.state).ok().and_then(|s| serde_json::from_str::<Value>(&s).ok()) {
        let mut st = sh.st.lock().unwrap();
        st.take_id = v["take_id"].as_u64().unwrap_or(0);
        st.name = v["name"].as_str().unwrap_or("").into();
        st.started_at = v["started_at"].as_f64().unwrap_or(0.0);
        st.output_path = v["output_path"].as_str().unwrap_or("").into();
        st.beat = v["beat"].as_i64().unwrap_or(0);
        st.beat_name = v["beat_name"].as_str().unwrap_or("").into();
    }
    log(&sh, &format!("studiod {} on 127.0.0.1:{} · obs {} · state {}", env!("CARGO_PKG_VERSION"), sh.cfg.port, sh.cfg.obs, sh.cfg.state));
    { let s = sh.clone(); std::thread::spawn(move || obs_loop(s, rx)); }
    { let s = sh.clone(); std::thread::spawn(move || beacon_loop(s)); }
    for c in listener.incoming().flatten() {
        let s = sh.clone();
        std::thread::spawn(move || http(s, c));
    }
}
