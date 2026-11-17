"""Voice follow: the prompter's highlight follows the reader's voice, graded by replaying a read through
app/ui/follow.js (scripts/follow-bench.mjs) and checking the acceptance numbers.

The read here is synthetic, built so every number is known: a script read at 3.3 words a second (a real reader's measured rate),
a recognizer that reports each word 0.35 s after it ends (Vosk's median partial lag on a real take, measured with the WASM
build the app runs), a noise floor at -60 dB and words at -20 dB for the VAD, a 2.5 s pause, a skipped sentence and an
ad-lib. A real take is not committed: it is a person's voice. Its numbers (median, p95, the stretches behind) are in the
PR that tuned follow.js, measured with the same bench on the recorded take.
"""
import json
import math
import random
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts" / "follow-bench.mjs"
RATE = 16000
LINES = [
    "Hi to the investment company in Chicago.",
    "I hope you are having a good day.",
    "You already have gold tables and an early server.",
    "So I built the piece that goes in front of them, one entitlement layer.",
    "Three license tiers, basic, pro, internal, and the fields each one may see.",
    "This one function decides what a caller gets, field by field.",
    "Every answer carries provenance: the table, the version, who approved it.",
    "Bronze, silver, gold, on the sample news feed.",
    "Reply here and I will send the next build.",
]
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


def words_of(lines):
    out = []
    for li, line in enumerate(lines):
        for w in line.split():
            w = "".join(c for c in w.lower() if c.isalnum())
            if w:
                out.append((li, w))
    return out


def build(tmp, skip_line=None, pause_after_line=None, adlib_after_line=None, lag=0.35, wps=3.3):
    """Speak LINES (minus skip_line) -> wav, recognizer events, truth word times."""
    rnd = random.Random(7)
    t, truth, spoken = 1.0, [], []
    for li, line in enumerate(LINES):
        if li == skip_line:
            continue
        ws = [w for (l, w) in words_of(LINES) if l == li]
        for w in ws:
            dur = 1 / wps - 0.06
            truth.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
            spoken.append(w)
            t += dur + 0.06
        if li == adlib_after_line:   # off-script words (the recognizer hears them; they are not in the script)
            for w in ("so", "basically", "yeah"):
                truth.append({"word": "~" + w, "start": round(t, 3), "end": round(t + 0.24, 3)})
                spoken.append(w)
                t += 0.3
        t += 2.5 if li == pause_after_line else 0.45
    end = t + 1.5
    # audio: -60 dB noise, -20 dB noise bursts where a word is spoken
    n = int(end * RATE)
    on = [False] * n
    for w in truth:
        for k in range(int(w["start"] * RATE), min(n, int(w["end"] * RATE))):
            on[k] = True
    frames = bytearray()
    for k in range(n):
        amp = 0.1 if on[k] else 0.001
        frames += struct.pack("<h", int(max(-1, min(1, rnd.gauss(0, amp))) * 32767))
    with wave.open(str(tmp / "read.wav"), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(RATE); f.writeframes(bytes(frames))
    # recognizer: every 64 ms the partial = the words of this utterance that ended `lag` s ago; a final after 0.6 s quiet
    ev, utt, done, last = [], [], 0, None
    step = 1024 / RATE
    k = 0
    while k * step < end:
        tt = (k + 1) * step
        ev.append({"t": round(tt + 0.02, 4), "k": "a", "s": round(tt, 4)})
        while done < len(truth) and truth[done]["end"] + lag <= tt:
            utt.append(truth[done]); done += 1
        quiet = utt and (done >= len(truth) or truth[done]["start"] > utt[-1]["end"] + 0.6) and tt >= utt[-1]["end"] + 0.6 + lag
        text = " ".join("[unk]" if w["word"].startswith("~") else w["word"] for w in utt)
        if quiet:
            ev.append({"t": round(tt + 0.02, 4), "k": "f", "s": text, "w": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in utt]})
            utt, last = [], None
        elif text != last:
            ev.append({"t": round(tt + 0.02, 4), "k": "p", "s": text}); last = text
        k += 1
    (tmp / "events.json").write_text(json.dumps(ev))
    (tmp / "truth.json").write_text(json.dumps([w for w in truth if not w["word"].startswith("~")]))
    (tmp / "lines.json").write_text(json.dumps(LINES))
    return truth


def bench(tmp, *extra):
    r = subprocess.run([node, str(BENCH), "--lines", str(tmp / "lines.json"), "--events", str(tmp / "events.json"),
                        "--wav", str(tmp / "read.wav"), "--truth", str(tmp / "truth.json"), "--json", *extra],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_lag_under_300_ms_median_and_600_p95(tmp_path):
    build(tmp_path)
    d = bench(tmp_path)
    assert d["placed"] >= 80
    assert d["median_ms"] < 300, d
    assert d["p95_ms"] < 600, d


def test_never_more_than_one_word_behind_for_300_ms(tmp_path):
    build(tmp_path, adlib_after_line=3)
    d = bench(tmp_path)
    # the first words of a read lock the tracker on; from then on it is never behind the reader
    assert [b for b in d["behind_over_300ms"] if b[0] > 2.5] == [], d["behind_over_300ms"]


def test_a_2_s_pause_holds_the_highlight(tmp_path):
    build(tmp_path, pause_after_line=4)
    d = bench(tmp_path)
    assert d["holds"], "the read has a 2.5 s pause"
    assert all(h["moves"] == 0 for h in d["holds"]), d["holds"]


def test_a_skipped_sentence_relocks_within_2_words(tmp_path):
    truth = build(tmp_path, skip_line=5)
    at = next(w["start"] for w in truth if w["word"] == "every")   # line 6 is the one after the skipped line 5
    d = bench(tmp_path, "--skip", str(at - 0.01))
    # 2 heard words re-lock it; the recognizer reports them 0.35 s late, so the reader has said 2 more by the time it shows
    assert d["skip"]["relock_words"] is not None and d["skip"]["relock_words"] <= 4, d["skip"]


def test_no_paid_service_or_key():
    """WHERE Vizier is built from the open-source repo, voice follow needs no account or key: the recognizer and the
    model are fetched from public URLs pinned by SHA-256, and follow.js talks to nothing but the mic."""
    js = (ROOT / "app" / "ui" / "follow.js").read_text()
    assert "fetch(this.modelUrl)" in js and "http" not in js.replace("https://", "").split("fetch(this.modelUrl)")[0][-200:]
    for bad in ("api_key", "apiKey", "Authorization", "Bearer", "wss://", "speechSynthesis", "webkitSpeechRecognition"):
        assert bad not in js, bad
    fetch = (ROOT / "scripts" / "fetch-voice.sh").read_text()
    assert fetch.count("_SHA=") == 3
    for url in ("https://registry.npmjs.org/vosk-browser/", "https://ccoreilly.github.io/vosk-browser/models/", "https://alphacephei.com/vosk/models/"):
        assert url in fetch
    assert "fetch-voice.sh" in (ROOT / "app" / "build.sh").read_text()
