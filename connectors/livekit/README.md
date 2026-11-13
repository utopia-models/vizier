# LiveKit connector

Two pieces, both off the shelf on the LiveKit side (no custom media server):

| file | runs on | does |
|---|---|---|
| `publish.html` | the machine you take calls on, in a Chromium-based browser or webview | joins a room and publishes your mic as track `me` and the call's audio as track `them` (screen-share audio: tick *share audio*, or *share system audio* on Windows) |
| `vizier_livekit.py` | next to the Vizier server | joins the same room hidden and listen-only, reads LiveKit's standard transcription (`lk.transcription` text streams, and the older `transcription_received` event) and posts every final line to `POST /lines` on the right side |

Something in the room has to transcribe: a LiveKit Agents worker with any STT plugin, a Pipecat bot on the LiveKit
transport, or any process that publishes `lk.transcription` with `lk.transcribed_track_id` set to the track it heard.

```bash
pip install -r requirements.txt
LIVEKIT_URL=wss://your-livekit LIVEKIT_API_KEY=… LIVEKIT_API_SECRET=… VIZIER_LK_ROOM=calls python vizier_livekit.py
```

`publish.html?url=<wss://livekit>&token=<join token>` (or `?token_url=<endpoint returning {"url","token"}>`), then
**Join**. Mint the join token on your own server; never put an API secret in the page.

Side of a line: the transcribed track's name (`me` / `them`), else the speaker's identity in `VIZIER_LK_ME` → me,
else them. Set `VIZIER_LK_INTERIM=1` to also draw non-final text live.
