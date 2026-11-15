"""Replay test (a user, twice: "why does it say camera might be blocked? Just use my laptop camera.").
The desk's Meet showed "camera might be blocked" because the laptop camera only went out when someone clicked Join call,
and the button is hidden since CALL mode went away. Now the server says when a call wants the camera (/health
cam_wanted, from VIZIER_CAM_WANTED_CMD) and the app joins by itself."""
import asyncio
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
HTML = (Path(__file__).resolve().parents[2] / "app" / "ui" / "index.html").read_text()


def load(tmp_path, monkeypatch, cmd=None):
    for k in list(os.environ):
        if k.startswith("VIZIER_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("VIZIER_DATA", str(tmp_path))
    monkeypatch.setenv("VIZIER_CAM_WANTED_TTL", "0")
    if cmd is not None:
        monkeypatch.setenv("VIZIER_CAM_WANTED_CMD", cmd)
    import vizier
    return importlib.reload(vizier)


def health(vz):
    async def go():
        async with TestClient(TestServer(vz.make_app())) as c:
            return await (await c.get("/health")).json()
    return asyncio.run(go())


def test_health_says_cam_wanted_when_the_command_exits_0(tmp_path, monkeypatch):
    flag = tmp_path / "meet-open"
    vz = load(tmp_path, monkeypatch, f"test -e {flag}")
    assert health(vz)["cam_wanted"] is False
    flag.touch()
    assert health(vz)["cam_wanted"] is True


def test_cam_wanted_is_false_with_no_command_or_a_broken_one(tmp_path, monkeypatch):
    assert health(load(tmp_path, monkeypatch))["cam_wanted"] is False
    assert health(load(tmp_path, monkeypatch, "exit 3"))["cam_wanted"] is False


def test_the_app_acts_on_cam_wanted_every_poll():
    body = re.search(r"async function pollHealth\(\) \{(.*?)\n\}", HTML, re.S).group(1)
    assert "autoCam(!!h.cam_wanted)" in body, "pollHealth must hand cam_wanted to autoCam"
    assert re.search(r'if \(a === "join"\) \{ autoJoined = true; join\(\); \}', HTML), "autoCam must call join()"


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_cam_step_joins_with_no_click_and_leaves_only_its_own_join():
    fn = re.search(r"function camStep\(.*?\n\}", HTML, re.S).group(0)
    cases = [  # wanted, live, auto, last -> action
        (True, False, False, False, "join"),    # the call opened: camera out, no click
        (True, True, True, True, "none"),       # steady: no churn every poll
        (False, True, True, True, "leave"),     # the call closed: our own join ends
        (False, True, False, True, "none"),     # the user clicked Join by hand: leave it alone
        (True, True, False, False, "none"),     # already live by hand when the call opens
        (False, False, False, False, "none"),
    ]
    js = fn + "\nconsole.log(JSON.stringify(" + json.dumps([c[:4] for c in cases]) + ".map(c => camStep(...c))));"
    out = json.loads(subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout)
    assert out == [c[4] for c in cases]
