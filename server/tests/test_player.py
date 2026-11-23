"""The clip player (knowledge#17355): synthetic 2 s clips, a real Chromium on /player, the cursor driven through
POST /prompter/position exactly as the app does. Acceptance: (1) clips 0..4 start in order within 300 ms of each cursor
event; (2) the cursor / key path is untouched by the player (state readback idx 0..5); (3) a missing clip is a visible
card + a banner in GET /state, cleared by the next row that has its clip."""
import asyncio, glob, importlib, os, shutil, subprocess, sys, time
from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pw = pytest.importorskip("playwright.async_api")


def chrome():
    for p in [os.environ.get("CHROME_BIN"), *sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome")), reverse=True),
              shutil.which("chromium"), shutil.which("google-chrome")]:
        if p and Path(p).exists():
            return p
    pytest.skip("no chromium")


@pytest.fixture
def vz(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    (tmp_path / "speaker-script.md").write_text("# T\n\n## 1 · open\nl0\nl1\nl2\nl3\nl4\nl5\n")
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("VIZIER_SPEAKER_SCRIPT", str(tmp_path / "speaker-script.md"))
    monkeypatch.setenv("VIZIER_CLIPS", str(tmp_path / "clips"))
    d = tmp_path / "clips" / "t"; d.mkdir(parents=True)
    for i, col in enumerate(["red", "green", "blue", "yellow", "magenta"]):   # rows 0..4 have clips; row 5 does not
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={col}:s=320x180:r=15:d=2",
                        "-c:v", "libvpx", "-b:v", "200k", str(d / f"row-{i}.webm")], check=True)
    import player, vizier
    importlib.reload(player)
    return importlib.reload(vizier), tmp_path


def pos(line):
    return {"part": 1, "parts": 1, "title": "speaker script", "line": line, "lines": 6, "text": f"l{line - 1}"}


def test_player_follows_the_cursor_and_flags_a_missing_clip(vz):
    v, tmp = vz
    async def go():
        srv = TestServer(v.make_app()); await srv.start_server()
        base = f"http://127.0.0.1:{srv.port}"
        async with pw.async_playwright() as p:
            b = await p.chromium.launch(executable_path=chrome(), args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"])
            page = await b.new_page(viewport={"width": 640, "height": 360})
            await page.goto(f"{base}/player?slug=t")
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async def post(line): return await (await s.post(f"{base}/prompter/position", json=pos(line))).json()
                async def rows(): return (await (await s.get(f"{base}/player/log?n=100")).json())["rows"]
                await asyncio.sleep(0.5)
                for line in range(1, 6):            # idx 0..4: the first row, then four Downs
                    t0 = time.perf_counter(); await post(line)
                    for _ in range(100):
                        if sum(r["event"] == "started" for r in await rows()) >= line: break
                        await asyncio.sleep(0.02)
                    assert time.perf_counter() - t0 < 1.5
                    await asyncio.sleep(0.4)
                started = [r for r in await rows() if r["event"] == "started"]
                assert [r["row"] for r in started] == [0, 1, 2, 3, 4]
                for r in started:
                    assert r["ms_from_cursor"] < 300, r
                    assert r["server_ms_from_cursor"] < 300, r
                print("start latency ms:", [(r["row"], r["ms_from_cursor"], r["server_ms_from_cursor"]) for r in started])
                # (3) row 5 has no clip: card + banner, never a silent still screen
                await post(6)
                for _ in range(100):
                    st = await (await s.get(f"{base}/state")).json()
                    if st.get("clip_banner"): break
                    await asyncio.sleep(0.02)
                assert st["clip_banner"]["text"] == "no clip for row 5"
                assert (await page.inner_text("#card")) == "no clip for row 5" and await page.is_visible("#card")
                # (2) the cursor is the app's alone: state readback is exactly what was posted
                assert st["current_line_index"] == 5
                await post(5)                         # Up onto row 4: replays it, banner clears
                for _ in range(100):
                    st = await (await s.get(f"{base}/state")).json()
                    if not st.get("clip_banner"): break
                    await asyncio.sleep(0.02)
                assert st["clip_banner"] is None and st["current_line_index"] == 4
                assert not await page.is_visible("#card")
            await b.close()
        await srv.close()
    asyncio.run(go())
