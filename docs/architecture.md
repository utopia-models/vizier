# How the pieces fit

Vizier is the screen. Everything that hears or thinks lives outside it and talks to it over one small HTTP API
([openapi.yaml](openapi.yaml)).

```
                ┌──────────── the machine you take calls on ────────────┐
                │  connectors/livekit/publish.html                      │
                │    track "me"   ◀ your microphone                     │
                │    track "them" ◀ the call's audio (screen-share audio)│
                └───────────────┬───────────────────────────────────────┘
                                ▼
                         LiveKit room ──▶ a transcriber (LiveKit Agents, Pipecat, sherpa-onnx, …)
                                │            publishes lk.transcription per track
                                ▼
         connectors/livekit/vizier_livekit.py   (listen-only; track name → me/them)
                                │ POST /lines
                                ▼
 your agent ── MCP ──▶ connectors/mcp ──▶  Vizier server  ──WS /feed──▶ desktop app · browser (/)
   reads the transcript (tool or pushed channel events), posts bullets and scripts
```

## Rules the server keeps

- **A line has a side.** `me` is the person using Vizier; `them` is everyone else. Sides come from where the audio
  was captured (two tracks), never from guessing who is talking.
- **Final lines are kept; interim text is not.** `final: false` is drawn under the transcript and replaced by the next
  one. `GET /transcript` and the MCP `read_transcript` tool return only final lines, each with a `seq`.
- **Posting opens a call.** A line or bullets with no call open starts one, so a connector never has to coordinate
  with an agent about who calls `/call/start`.
- **Seq restarts per call.** A window that connects mid-call gets the call's last 8 lines; windows dedupe by `seq`.
- **No audio, no keys.** The server never receives audio and never calls a model, so it holds no API keys. Set
  `VIZIER_TOKEN` to stop anyone else on the network from posting to your screen.

## Latency

`POST /lines` takes an optional `t_audio_end` (epoch seconds when the speech ended); the line then carries `final_s`
(time from end of speech to the line on the server), and `POST /bullets` with `t_audio_end` shows the time to the
bullets on the card. Both land in `$VIZIER_LOG` (jsonl), so p50/p90 are read from the log, not estimated.
