#!/usr/bin/env python3
"""vizier_livekit.py: show a LiveKit room's transcript on Vizier.

Joins a LiveKit room as a hidden, listen-only participant and posts every transcription the room carries to the
Vizier server's POST /lines. It reads LiveKit's standard transcription stream (text streams on topic
`lk.transcription`, which LiveKit Agents, Pipecat's LiveKit transport and any other transcriber can publish) and the
older `transcription_received` event. It does no speech-to-text itself: something else in the room transcribes.

Which side a line is on (ME or THEM), first rule that matches:
  1. the transcribed track's name is `me` or `them` (the publisher page in this folder names its two tracks so);
  2. the speaking participant's identity is listed in VIZIER_LK_ME (comma-separated) -> me;
  3. otherwise -> them.

env:
  LIVEKIT_URL                   ws(s)://host:7880
  LIVEKIT_API_KEY / _SECRET     to mint this participant's token   (or LIVEKIT_TOKEN: a ready token)
  VIZIER_LK_ROOM                the room to join (default vizier)
  VIZIER_LK_IDENTITY            this participant's identity (default vizier-display)
  VIZIER_LK_ME                  identities whose speech is ME
  VIZIER_LK_INTERIM             "1" = also post non-final hypotheses as live interim text
  VIZIER_URL / VIZIER_TOKEN     the Vizier server (default http://127.0.0.1:8786)

    pip install -r requirements.txt && python vizier_livekit.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
import urllib.request

from livekit import api, rtc

log = logging.getLogger("vizier-livekit")
URL = os.environ.get("LIVEKIT_URL", "ws://127.0.0.1:7880")
ROOM = os.environ.get("VIZIER_LK_ROOM", "vizier")
IDENTITY = os.environ.get("VIZIER_LK_IDENTITY", "vizier-display")
ME = {x.strip() for x in os.environ.get("VIZIER_LK_ME", "").split(",") if x.strip()}
INTERIM = os.environ.get("VIZIER_LK_INTERIM", "") == "1"
VIZIER = os.environ.get("VIZIER_URL", "http://127.0.0.1:8786").rstrip("/")
VIZIER_TOKEN = os.environ.get("VIZIER_TOKEN", "")
TOPIC = "lk.transcription"


def token() -> str:
    if os.environ.get("LIVEKIT_TOKEN"):
        return os.environ["LIVEKIT_TOKEN"]
    return (api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
            .with_identity(IDENTITY).with_name("Vizier")
            .with_grants(api.VideoGrants(room_join=True, room=ROOM, can_publish=False, can_publish_data=False,
                                         can_subscribe=True, hidden=True))
            .to_jwt())


def post(path: str, body: dict) -> None:
    req = urllib.request.Request(VIZIER + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    if VIZIER_TOKEN:
        req.add_header("Authorization", "Bearer " + VIZIER_TOKEN)
    urllib.request.urlopen(req, timeout=5).read()


class Bridge:
    def __init__(self, room: rtc.Room):
        self.room = room
        self.posted: set[str] = set()          # segment ids already posted as final (both paths can carry one)

    def side(self, identity: str, track_sid: str | None) -> str:
        p = self.room.remote_participants.get(identity)
        pubs = [p.track_publications.get(track_sid)] if p and track_sid else []
        if track_sid and not any(pubs):        # the transcriber may name a track another participant published
            pubs = [q.track_publications.get(track_sid) for q in self.room.remote_participants.values()]
        for pub in filter(None, pubs):
            if pub.name in ("me", "them"):
                return pub.name
        return "me" if identity in ME else "them"

    async def line(self, ch: str, text: str, final: bool, seg_id: str, source: str) -> None:
        text = text.strip()
        if not text or (final and seg_id in self.posted):
            return
        if not final and not INTERIM:
            return
        if final:
            self.posted.add(seg_id)
        body = {"ch": ch, "text": text, "final": final, "source": source[:64]}
        if final:
            body["t_audio_end"] = time.time()   # upper bound: when the room delivered it
        try:
            await asyncio.to_thread(post, "/lines", body)
        except Exception as e:  # noqa: BLE001 - Vizier restarting: the line is dropped, the bridge stays up
            log.warning("post /lines failed: %s", e)

    async def on_stream(self, reader: rtc.TextStreamReader, identity: str) -> None:
        a = reader.info.attributes or {}
        text = await reader.read_all()
        final = str(a.get("lk.transcription_final", "true")).lower() == "true"
        track = a.get("lk.transcribed_track_id")
        await self.line(self.side(identity, track), text, final, a.get("lk.segment_id") or reader.info.stream_id,
                        f"livekit:{identity}")

    async def on_segments(self, segments, participant, publication) -> None:
        identity = participant.identity if participant else ""
        sid = publication.sid if publication else None
        for s in segments:
            await self.line(self.side(identity, sid), s.text, bool(s.final), s.id, f"livekit:{identity}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    room = rtc.Room()
    b = Bridge(room)
    room.register_text_stream_handler(TOPIC, lambda r, ident: asyncio.ensure_future(b.on_stream(r, ident)))
    room.on("transcription_received", lambda segs, p, pub: asyncio.ensure_future(b.on_segments(segs, p, pub)))
    stop = asyncio.Event()
    room.on("disconnected", lambda *_: stop.set())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await room.connect(URL, token(), rtc.RoomOptions(auto_subscribe=False))
    log.info("in room %s as %s -> %s", ROOM, IDENTITY, VIZIER)
    await stop.wait()
    await room.disconnect()


if __name__ == "__main__":
    asyncio.run(main())   # exits when the room drops; run it under a supervisor that restarts it
