# Vizier

**A display-only teleprompter and call screen that any agent can drive over HTTP or MCP.**

Vizier is one always-on-top window, hidden from screen capture, for people who talk to clients, interviewers or a
camera. During a call it shows the live transcript (your side and theirs) and, on top, two or three short bullets you
can say next. Between calls the same window is a teleprompter: a script that scrolls under a reading line, with
presenter-clicker keys and an API an agent uses to follow you and move the script.

Vizier is **only a display**. It does no listening, no speech-to-text and no language-model work: it shows what is
posted to its HTTP API. Your agent, a LiveKit room, a script or `curl` does the listening and the thinking, and posts
lines, bullets and scripts; every open window redraws at once. That keeps Vizier small, lets you choose your own
speech-to-text and model, and lets any agent drive it.

```
 your call ─▶ (any transcriber) ─▶ POST /lines ───────┐
 your agent ─────────────────────▶ POST /bullets ─────┼─▶ Vizier server ─▶ WS /feed ─▶ desktop app / browser
 your agent ─────────────────────▶ POST /script ──────┘          ▲
 an MCP client (Claude Code, …) ─▶ connectors/mcp ───────────────┤
 a LiveKit room's transcription ─▶ connectors/livekit ───────────┘
```

| folder | what it is |
|---|---|
| [`server/`](server/) | the server: one Python (aiohttp) process with the display API, the feed WebSocket and a browser view |
| [`app/`](app/) | the desktop app: one always-on-top Tauri window (Windows, macOS), hidden from screen capture |
| [`connectors/mcp/`](connectors/mcp/) | an MCP server, so any MCP-capable agent drives the overlay with tools |
| [`connectors/livekit/`](connectors/livekit/) | puts a LiveKit room's transcription on the overlay; a page that publishes both sides of a call into a room |
| [`examples/`](examples/) | curl and Python examples |
| [`docs/`](docs/) | the [OpenAPI spec](docs/openapi.yaml) of the API, and how the pieces fit |

## Quick start

```bash
cd server
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python vizier.py                       # http://127.0.0.1:8786
```

Open <http://127.0.0.1:8786/> in a browser (or the desktop app), then post to it:

```bash
curl -s 127.0.0.1:8786/lines   -H 'Content-Type: application/json' -d '{"ch":"them","text":"What is your rate?"}'
curl -s 127.0.0.1:8786/bullets -H 'Content-Type: application/json' -d '{"bullets":["My rate is 90 an hour.","What is the timeline?"]}'
curl -s '127.0.0.1:8786/transcript?format=text'
```

`ch` is `me` (the person using Vizier) or `them` (anyone else on the call). The whole API is in
[`docs/openapi.yaml`](docs/openapi.yaml) (also served at `/openapi.yaml`).

## Let an agent drive it (MCP)

[`connectors/mcp/vizier_mcp.py`](connectors/mcp/vizier_mcp.py) is a standard-library-only MCP server (stdio). Add it
to any MCP client. For Claude Code:

```bash
claude mcp add vizier -e VIZIER_URL=http://127.0.0.1:8786 -- python3 /path/to/vizier/connectors/mcp/vizier_mcp.py
```

| tool | does |
|---|---|
| `post_line {ch, text, final?}` | shows a transcript line (`me` / `them`); `final=false` is live interim text |
| `post_bullets {bullets, for?}` | shows 1-6 short bullets of what to say next, on top of the overlay |
| `read_transcript {since?}` | the open call's final lines, oldest first |
| `set_script {markdown}` | puts a script on the teleprompter (`## ` block per take, one line per breath) |
| `set_status {text}` | the overlay's one-line status |
| `set_note {text}` | the teleprompter's one-line note |
| `start_call {call_id?, title?}` · `end_call` | open / close a call |

With `VIZIER_MCP_PUSH=1` it also pushes each new transcript line into a Claude Code session as a channel event, so
the agent hears the call as it happens instead of polling `read_transcript`.

## Let an agent drive the teleprompter

The window reports where the reader is, and an agent that hears them keeps the script in step:

| endpoint | does |
|---|---|
| `GET /prompter/position` | the current part, line and its text, with its age in seconds |
| `POST /prompter/goto {"part", "line"}` | jumps the prompter there (1-based) |
| `POST /prompter/follow {"on"}` or `{"toggle": true}` | the agent's speech-follow on / off (**Alt+F** in the app) |
| `POST /prompter/snap` | the app writes a picture of what it shows (`%PUBLIC%\vizier\live.png`), since the window is hidden from capture |
| `POST /prompter/note {"text"}` | the one-line note under the script |

## Transcribe a call with LiveKit

1. Run a [LiveKit](https://livekit.io) server (self-hosted or cloud) and any transcriber that publishes LiveKit's
   standard transcription (`lk.transcription` text streams): a LiveKit Agents worker, a Pipecat bot, or your own.
2. Open [`connectors/livekit/publish.html`](connectors/livekit/publish.html) on the machine you take calls on. It
   publishes your mic as track `me` and the call's audio (screen-share audio) as track `them`.
3. Run [`connectors/livekit/vizier_livekit.py`](connectors/livekit/vizier_livekit.py): it joins the room listen-only
   and posts every final line to Vizier on the right side.

See [`docs/architecture.md`](docs/architecture.md).

## Configuration

Everything is an environment variable; `--config <file>` (or `$VIZIER_CONFIG`, or `./vizier.env`) loads a
`KEY=VALUE` file first, and a variable already set in the environment wins. See [`vizier.env.example`](vizier.env.example).

| variable | meaning | default |
|---|---|---|
| `VIZIER_HOST` / `VIZIER_PORT` | listen address | `127.0.0.1` / `8786` |
| `VIZIER_TOKEN` | when set, every POST needs `Authorization: Bearer <token>` | — (open) |
| `VIZIER_DATA` | data directory: posted script, takes, event log | `~/.vizier` |
| `VIZIER_SCRIPTS` | the prompter's starting script (markdown, one `## ` block per take) | `$VIZIER_DATA/scripts.md` |
| `VIZIER_CAM` | an RTSP camera stream the prompter's **R** key records takes from (needs ffmpeg) | — (off) |
| `VIZIER_ALLOWED_ORIGINS` | extra CORS origins (comma-separated) besides the desktop app's | — |

The desktop app finds the server, in order: `?server=` · the ⚙ field (saved locally) · `VIZIER_SERVER` env ·
`vizier.json` beside `vizier.exe` · `config.json` in the app's config directory · `http://127.0.0.1:8786`.

```json
{ "server": "https://vizier.example.com", "media": "https://media.example.com", "camPath": "cam", "callPath": "call-out" }
```

`hideLabels` (optional) lists author names whose labels the prompter drops from a script: `"(name, date)"` in a
part title and `"name:"` before a line.

`media` is an optional WebRTC server (for example MediaMTX) for **Join call**: it takes your camera over WHIP at
`<media>/<camPath>/whip` and plays the call's audio over WHEP from `<media>/<callPath>/whep`.

## Desktop app

Download `vizier.exe` from [Releases](https://github.com/utopia-models/vizier/releases). Releases are Authenticode-signed;
see [`signing/`](signing/) for the certificate and how to verify it. To build it yourself:

```bash
scripts/setup-xwin.sh      # Debian/Ubuntu: clang/lld/llvm, the MSVC Rust target, cargo-xwin
app/build.sh               # -> app/target/x86_64-pc-windows-msvc/release/vizier.exe
VIZIER_PROFILE=fast app/build.sh   # a quick rebuild: no LTO, only the app crate recompiles (-> .../fast/vizier.exe)
```

On macOS or Windows, `cargo build --release` in `app/`. `vizier.exe --selftest` presses the prompter keys and times
each one (keydown to painted frame), opens the mic once, and writes `%PUBLIC%\vizier\selftest.txt` (with the window's
display affinity, `0x11` = hidden from capture) and `selftest.png` (what the page shows, from WebView2 itself, since
the window is excluded from screen capture). `scripts/windows/` has a launcher and a window pinner for a machine you
drive over SSH.

### Prompter keys

The script scrolls continuously; the current line is the one on the reading line (the arrow, a fifth of the way down).
SCRIPT and CONTEXT share one type size (32 px by default). Speed and type size are remembered. Presenter clickers send PageDown/PageUp and B, `.` or F5, so they work as-is.

| key | does |
|---|---|
| Space, P, B, `.`, F5, Enter | play / pause the scroll (never starts a recording) |
| R | start a take of the shown part |
| mouse wheel / trackpad | scroll by hand; pauses the auto-scroll |
| PageDown / PageUp, ↓ / ↑ | next / previous line, also while scrolling |
| Alt+↓ / Alt+↑ | the same from any app (global shortcut); they scroll CONTEXT / DO NOT SAY too |
| Tab / Alt+T | next tab: SCRIPT, CONTEXT, DO NOT SAY |
| Tab, ↑, ↓ (plain) | global in the desktop app: they work from any window, on that laptop only. Alt+K releases them and takes them again |
| `+` `=` `]` / `-` `_` `[` | faster / slower (×1.15) |
| Ctrl + / Ctrl − | bigger / smaller type (SCRIPT and CONTEXT together) |
| Home | top of the part |
| → / ← | next / previous part (starts a new take file while recording) |
| Esc | stop the scroll and the recording |

### Talk to an agent: Ears (Alt+E) and Mouth (Alt+M)

Vizier can put you in a [LiveKit](https://livekit.io) room with an agent, so you talk to it from the laptop. Add two keys to
`config.json` (app config dir) or `vizier.json` beside the exe:

```json
{ "talkUrl": "wss://livekit.example.com", "talkToken": "<a join token for the agent's room>" }
```

A small pill shows **Ears ●  Mouth ●** in the top-right corner. From any app, **Alt+E** turns Ears on (your mic goes
into the room) and off again; **Alt+M** turns Mouth on (you hear the agent's replies) and off. On a call: Ears on, Mouth
off. The switches are also this participant's LiveKit attributes (`ears`, `mouth` = `on`/`off`), so the agent side can
read them with any LiveKit SDK. Vizier itself does no speech-to-text: an agent process in the room transcribes and
answers. Without `talkUrl`/`talkToken` there is no pill and nothing joins. Code: `app/ui/talk.js`.

## Development

```bash
cd server && pip install -r requirements-dev.txt && pytest -q
scripts/scrub-check.sh     # no secrets (gitleaks, full history) and no private deployment paths
```

The server and the connectors run on Python 3.9+; the MCP connector needs nothing outside the standard library.

CI runs all three (tests, scrub, Windows build) on every push. Tag `vX.Y.Z` (matching `app/Cargo.toml`) to publish
a signed release. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md).

## License

[Apache License 2.0](LICENSE), with attribution in [NOTICE](NOTICE) · © 2026 Utopia Models, Inc.

Vizier and Utopia Models are names of Utopia Models, Inc.; the license grants no trademark rights (Apache-2.0 §6).
