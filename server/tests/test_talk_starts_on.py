"""Replay test (the deployment's issue 13786, 2026-11-15): scout's voice was silent on the laptop. talk.js started Ears and Voice from
localStorage, so a fresh WebView profile (a new build swapped in) or an Alt+M from an earlier session came up with Voice off:
the laptop sent mouth=off and unsubscribed from the agent's audio, and nothing on screen said why. the user: "I need Scout on
demand at all times". Both start ON at every launch; the agent's ears process decides when it speaks (it answers only a
line that names it), and vizier.json "talkStartOff" is the one opt-out."""
import re
from pathlib import Path

TALK = (Path(__file__).resolve().parents[2] / "app" / "ui" / "talk.js").read_text()


def test_ears_and_mouth_start_on():
    m = re.search(r"const state = \{([^}]*)\}", TALK)
    assert m, "no state object in talk.js"
    assert re.search(r"\bears:\s*true\b", m.group(1)) and re.search(r"\bmouth:\s*true\b", m.group(1)), m.group(1)


def test_a_kept_switch_is_never_the_start_state():
    assert not re.search(r"localStorage\.getItem\(KEEP\)", TALK), "a switch from an earlier session must not silence this one"


def test_start_off_is_read_from_the_config_once_per_launch():
    body = TALK[TALK.index("async function join("):]   # start() is the rejoin gate (#34); join() holds the start state
    body = body[: body.index("serverBase")]
    assert "cfg.talkStartOff" in body and "started" in body
