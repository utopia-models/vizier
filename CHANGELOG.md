# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Reader's own keys, lead marker, QC tab (ask 821).** Voice-follow is OFF by default (server `FOLLOW` and the page; Alt+F still turns it on). On the SPEAKER SCRIPT tab the global Up / Down step the current line and a `▶ NEXT` marker sits one line ahead; only his keys move the cursor, and each step is reported to `POST /prompter/position`. A new read-only QC tab shows the QA checklist (`VIZIER_QC_FILE`) and the last vsl_qa verdict (`VIZIER_QA_VERDICT`) from `GET /qc`. `scripts/windows/keys-sendinput.ps1` proves Down / Up / Alt+E / Alt+M with synthetic SendInput keys while the foreground window is full screen.
- **The agent writes the script tabs itself: `POST /speaker-script`, `POST /horus-script`, `GET /state`, and the `vizier-say` CLI.** A POST (JSON `{"text"}` or the raw text) replaces the live file the tab and vsl-follow already read, atomically (temp file + rename), and answers `{ok, bytes, sha, at}`; the open tab shows it on its next 2 s poll. `GET /state` is one JSON: his script (text, sha, mtime), the cue sheet summary with the current cue, the take (name, recording), and the front tab (the app now reports it to `POST /state/front`). Writes need the `X-Vizier-Write` header to match the file named by `VIZIER_WRITE_SECRET_FILE` (mode 0640, group `fleet-vizier-write`; read per request, never logged); without it the server stays read-only. Every write is appended to `$VIZIER_DATA/script-writes.jsonl` (who, when, sha) and the previous text is kept, last 20, with `POST /<tab>/undo` to restore it. An empty text is refused so a bad call cannot blank the tab. `scripts/vizier-say` is the CLI: no args reads `/state`, a file or stdin writes the speaker script, `--horus` the cue sheet, `--undo` restores.
- **Voice follow: the prompter highlights the word you are saying, from open-source parts**. Vosk runs in
  the window as WebAssembly on the mic; a cadence clock (your measured words per second, paused by a voice-activity
  detector) moves the highlight and the recognized words correct it, placing it on or ahead of your voice. Replayed on a
  recorded 146 s read in the real page: median lag -576 ms (ahead), p95 403 ms, no move during a 2.4 s pause. It replaces
  the agent-side follower that jumped whole lines from final utterances about 3 s behind. `scripts/fetch-voice.sh` fetches
  the recognizer and the model (pinned by SHA-256); `scripts/follow-bench.mjs` and `scripts/follow-e2e.py` measure it.

### Changed
- **HORUS SCRIPT lights the current cue within 0.4 s, not 2 s**: the tab polls `/horus-script` every 400 ms while it is open, so the lit row follows vsl-follow's `state.json` instead of trailing it by up to a 2 s poll.
- **No 'changed HH:MM' marks on the SPEAKER SCRIPT and HORUS SCRIPT tabs.** The server sends `changed: null` on every line and cue and the page renders nothing for it. The app embeds `app/ui/index.html`, so the exe needs a rebuild to drop the marks it already shows.
- **The window is the reader's: drag it by its header, resize it by its edges, minimize it and it stays minimized.** The
  header is a drag region (`data-tauri-drag-region`), the window has a 480x270 floor, and every move and resize is written
  to `window.json` beside the exe, which the next launch opens at (a rect that meets no monitor falls back to the bottom-left
  slot). The 2 s keeper no longer un-minimizes: a minimize by Alt+V, the taskbar or Win+D sticks until Alt+V or the taskbar
  brings it back (the reader, 2026-11-17: "when I do Alt+V to minimize Vizier, it comes back after 10 seconds"; "I would
  like to be able to move Vizier around... and change the size of it"). `scripts/windows/vizier-pin.ps1` matches: it keeps
  Vizier running and topmost, never its place, size or minimized state, and remembers the rect in the same file.

### Fixed
- **The talk room is joined once at a time, and a failed join backs off.** A failed connect scheduled a retry in 5 s and
  its own cleanup disconnect scheduled another in 3 s, so the joins doubled every round: 2855 "join failed" lines in 73 s
  (about 100 a second) while the room's server was unreachable on 2026-11-17, and the WebView2 renderer crashed, leaving a
  dead bar. Now one join is in flight at a time, one retry is armed at a time, the wait is 5 s doubling to 60 s with up to
  25% jitter, and a successful join resets it (`app/ui/backoff.js`, unit-tested with `node --test app/ui/backoff.test.mjs`).
- **`POST /prompter/snap` works in every mode** (issue 14117). The app wrote `live.png` only while RECORD showed a
  script, because the snap check sat inside the RECORD-only goto poll; on any other tab, or with no script, a snap request
  only moved the counter. It now has its own 400 ms poll, so laptop-eye on Ra (which bumps it every 2 s and draws
  `live.png` into its frame of the laptop, the window being excluded from capture) sees what Vizier shows, whatever tab.
- **The SCRIPT tab shows only the spoken lines, at a readable size, in a smaller window** (the user 2026-11-16 3:13 AM MT,
  recording a custom VSL from an outline: "I'm barely able to read the script. The text is extremely large... literally taking
  up half the screen... there's this section called coach on part... build", vizier#15; #16-#20). The coach bar, build log,
  part heading and on-screen cues are hidden on SCRIPT. The type size is 22 px by default under a new store key (a drifted
  stored size is dropped), capped at 40 px; the reading line sits at 10 %. The window opens as the bottom-left quarter of the
  primary monitor, is resizable, and stays where the reader puts it. `parse_takes` (and the page, the same rule) drops
  headings, `>` coaching notes, `Coach:` / `Coach on part N:` / `Note:` / `Direction:` / `Cue:` labels, rules, whole-line
  `[...]` / `(...)` directions and `[show]` / `[scout]` cues inside a part, and strips list markers, so an agent's line numbers
  stay the screen's and the arrows never step onto a hidden paragraph. Header HUD and key legend are one row each. A part
  built while the prompter was hidden is built again when it shows. Replay test `server/tests/test_spoken_lines.py`; the
  smoke test checks only spoken lines render and counts the rows that fit.

### Added
- **Talk: device self-check** (`vizierTalk.selfCheck()`). Runs on joining the room, on every device change and on every
  entry to the agent's `conversation` mode. It checks three things. **Playback:** the agent's audio plays on a speaker
  output, never a Hands-Free, headset or DJI one, and is pinned there with `setSinkId`. **Mic:** Ears uses the DJI mic,
  above digital silence. **Heard:** the raw mic rises 10 dB within 1 s of the agent's next line. No tone is played: the
  agent's own voice is the probe. Rows go to `POST /talk/selfcheck` (kept in `DATA/talk-selfcheck.jsonl`; `GET` returns
  the last run). The server adds a floor row (`VIZIER_FLOOR`, `VIZIER_FLOOR_OTHER`) and has the voice agent speak one
  fixed line (`VIZIER_VOICE_URL` `/say`) only for a failure with no fix. `POST /talk/selfcheck/run` asks the app to
  run it.
- **A smoke test every build runs before it is staged** (`scripts/smoke/smoke.py`): the built exe on the real Windows box
  with `--smoke` (WebView2 DevTools port, own profile, no global shortcuts; `smoke_key` presses Alt+V / Alt+R / Alt+E /
  Alt+M through the same `hotkey()` the shortcuts run), driven with real key and wheel input against a throwaway server
  with a dry VSL switch. Screenshot per step; exit 0 only when all pass.

### Fixed
- **Tab reaches SCRIPT**: TALK, SETTINGS, DESKS, AGENTS, SCRIPT, then TALK again. Tab works on SCRIPT with no script loaded.
- **The selected row follows the arrows** in SETTINGS and DESKS (`scrollIntoView({block: "nearest"})` each time the
  selection moves, clear of the key legend); before, Down moved the selection below the window and the list stayed put.
- **Ears publishes with DTX and RED off** (plus `voiceIsolation`, mono, the speech preset; echo cancellation, noise
  suppression and AGC stay on). What the agent decoded from a quiet laptop mic was 536 Hz bursts at -30.7 dBFS RMS.
- **Alt+R records a VSL take** from any app, and the SETTINGS panel's first row ("Recording") does the same by keyboard. 
  Both, and an agent's own "record", `POST /vsl` (`{"toggle": true}` or `{"on": bool}`): the server answers at once and
  runs `VIZIER_VSL_START_CMD` / `VIZIER_VSL_STOP_CMD` in the background, one at a time; `VIZIER_VSL_STATE` (a file that
  exists while a take records) is the truth, and `/health` carries it as `vsl`, so the red REC pill and the row always
  agree. Alt+R is native (main.rs POSTs on its own thread); the page paints the asked-for state in the same frame.

### Fixed
- **Tab, Alt+E, Alt+M and the arrows act at once while another app is in front.** WebView2 treated the on-screen,
  always-on-top window as backgrounded/occluded behind the foreground app: timers parked and frames not drawn, so a key's
  eval ran but nothing showed until a window change woke the page (Alt+V, a native minimize/restore, was the one key that
  did). The WebView now runs with occlusion backgrounding and timer throttling off, the 2 s keeper re-marks it visible
  if it drifted, every poll waits for its own last answer (no request pile-up on the page's connections), and SETTINGS
  changes show in the same frame and sync in the background.
- **Alt+V Alt+V minimizes Vizier** from any app; the same again brings it back. Before, the window keeper un-minimized
  the window every 2 s, so a minimize never stuck. Parked this way, the keeper leaves it minimized (content protection
  still holds); restored from the taskbar, it is kept on screen as before after 3 s. One Alt+V does nothing. No rule
  about when Vizier shows: it still opens on screen. Tests: `app/src/park.rs` (`rustc --test`), `server/tests/test_minimize.py`.

### Fixed
- **The prompter's script survives a restart.** `POST /script` records its choice in `$VIZIER_DATA/current-script` and a
  restarted server shows it again, instead of falling back to `VIZIER_SCRIPTS` (a deploy-time default that had become a
  past client's script and kept coming back on top). `POST /script {"clear": true}` takes a script down: the prompter
  shows "no script yet". Replay test: `server/tests/test_script_survives_restart.py`.

### Added
- **Talk: loopback check.** `await vizierTalk.loopback()` plays a short tone through each audio output (`setSinkId`) and
  measures it on the mic, raw and with echo cancellation on: per device `heard` (12 dB over the room) and `cancelled_db`.
  The last report is on the server at `GET /talk/loopback`. It shows which speaker the agent's voice leaks back from.

### Added
- TALK mode: the overlay's default when no take is recording and no call is live (it said CALL while you were only talking to
  your agent). A plain live transcript, your speech and the agent's spoken replies, newest at the bottom, partials shown
  live and replaced by the final. `GET/POST /talk/transcript` on the server; `connectors/talk/lines_forwarder.py` (+ a
  systemd user unit) feeds it from a voice gateway's `lines.jsonl`. The CALL and RECORD tabs are unchanged.
- MCP connector: `VIZIER_MCP_PUSH_CH` picks which sides are pushed as channel events (`me,them` by default). Set it to
  `them` when the agent already hears the user's own speech another way, so ME lines are not delivered twice.

### Fixed
- MCP connector: the first lines of a call were never pushed. `POST /lines` starts a call, and the pusher skipped
  every new call to its current end. Now only a call that was already running when the connector started is skipped.

## [0.2.0] - 2026-11-13

First public release. The repository starts a fresh history at this version.

### Changed
- **Vizier is now only a display.** The server no longer listens, transcribes or calls a model: it shows what is
  posted to its API. Removed: the `/ears/*` audio endpoints, the built-in speech-to-text and the Claude bullet writer,
  the take review, `scripts/desk-ears.sh` and every credential setting (`ANTHROPIC_API_KEY`, `VIZIER_CREDS`,
  `VIZIER_MODEL`, `VIZIER_USER_NAME`, `VIZIER_RULES_EXTRA`, `VIZIER_KEYTERMS`).
- Take recording is off unless `VIZIER_CAM` is set (it was on, against a default stream URL).
- The app's CLIENT tab is gone, and so is its built-in list of author names to hide: list them in the app's config
  as `"hideLabels": [...]`.

### Added
- Display API: `POST /lines`, `GET /transcript`, `POST /bullets`, `POST /status`, `POST /script`,
  `POST /prompter/note`, and an OpenAPI 3.1 spec (`docs/openapi.yaml`, served at `/openapi.yaml`).
- `VIZIER_TOKEN`: a bearer token required on every POST.
- `connectors/mcp/`: an MCP server (standard library only) with tools to post lines and bullets, set the script,
  status and note, and read the transcript; `VIZIER_MCP_PUSH=1` pushes new lines into a Claude Code session.
- `connectors/livekit/`: a bridge from a LiveKit room's standard transcription to `/lines`, and a page that publishes
  both sides of a call (mic = `me`, screen-share audio = `them`) into a room.
- `examples/` (curl, a minimal agent loop) and `docs/architecture.md`.
- A window that connects mid-call receives the call's last 8 lines; the app and the browser view dedupe by `seq`.
- Desktop app: talk to an agent in a LiveKit room. **Alt+E** toggles Ears (your mic into the room), **Alt+M** toggles
  Mouth (the agent's replies); both persist across restarts, publish as participant attributes, and every switch is
  logged through `POST /talk/log`. The agent's audio is muted while you speak (barge-in).
- Prompter: global keys (Alt+↓/↑ from any app; plain Tab, ↑, ↓, ←, → global, released and retaken with Alt+K),
  Alt+T next tab, a key legend, cue styling for `[show]` lines, 32 px type.
- Prompter driving API for agents: `POST/GET /prompter/position` (where the reader is), `POST /prompter/goto` (jump to a
  part and line), `POST /prompter/follow` (Alt+F toggles speech-follow), `POST /prompter/snap` (the app writes a
  picture of the prompter, since the window is hidden from screen capture).

## [0.1.1] - 2026-11-12

### Added
- App prompter for recording: continuous scroll at a set speed (px/s) with a reading line and the current line
  highlighted; keyboard and presenter-clicker control (play/pause, line step, speed, type size); see README.
- App RECORD tabs SCRIPT | CONTEXT | DO NOT SAY (`/prompter/context`), a BUILD strip (`/buildlog`), and a CLIENT tab
  shown when the server serves `/prompter/clients`.
- `vizier.exe --selftest` times each prompter key (keydown to painted frame), reports the window's display affinity,
  and writes `selftest.png` from WebView2's CapturePreview.
- Cargo profile `fast` (`VIZIER_PROFILE=fast app/build.sh`): a rebuild recompiles only the app crate.

### Changed
- Prompter type: SCRIPT and CONTEXT share one smaller size (18 px default, CSS `--type`, Ctrl +/− moves both); the
  2nd part of a real script shows 10 lines where 44 px showed 5. The reading line sits a fifth of the way down.
- A recording take never starts on its own: Space only plays/pauses the scroll; R starts a take (app and
  `prompter.html`).
- Mouse wheel / trackpad scroll pauses the auto-scroll (manual always wins); wheel line-mode deltas scale to the type.
- Author attribution ("(name, …)" in a title, "name:" before a line) is never shown.
- Less work behind the cursor: the scroll's animation loop runs only while playing (0 frames/s paused, was 60), and the
  2 s window check reads first and re-applies capture exclusion / topmost only when they drifted (was
  SetWindowDisplayAffinity + SetWindowPos every tick).
- `--selftest` also reports the idle frame rate.
- The app window is always hidden from screen capture (WDA_EXCLUDEFROMCAPTURE), checked every 2 s; the toggle is
  gone. The script poll is every 2 s (was 5 s).

## [0.1.0] - 2026-11-12

### Added
- First release as its own repository: the server (`server/vizier.py`), the desktop app (`app/`, Tauri, Windows x64
  build), the prompter and desk-link pages.
- Configuration by environment variables or a `KEY=VALUE` file (`--config`, `$VIZIER_CONFIG`, `./vizier.env`),
  with a single data directory (`VIZIER_DATA`).
- `ANTHROPIC_API_KEY` support for the Claude calls, besides the OAuth credentials file.
- Desktop app reads its server/media URLs from `VIZIER_SERVER` / `vizier.json` / its config dir.
- CI: server tests, a secret and private-path scrub, and a Windows cross-build; tagged releases are Authenticode-signed.
