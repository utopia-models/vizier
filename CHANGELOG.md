# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
