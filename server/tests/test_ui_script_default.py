"""Replay test (Tyler 2026-11-14): the Script / Context tabs, Tab to switch, arrows to step and the helper bar must not
disappear again. TALK became the default mode and hid all of them; the default is the SCRIPT view now."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "app" / "ui" / "index.html").read_text()


def test_default_mode_is_the_script_view():
    m = re.search(r"function autoMode\(\) \{ return (.*?); \}", HTML)
    assert m, "autoMode() missing"
    assert m.group(1).endswith(': "rec"'), "no recording and no call must land on the SCRIPT view, not TALK"


def test_script_and_context_tabs_exist():
    assert 'data-t="script"' in HTML and 'data-t="context"' in HTML


def test_helper_bar_lists_the_keys():
    bar = re.search(r'<div id="keylegend">(.*?)</div>', HTML, re.S).group(1)
    for k in ("↑ ↓", "← →", "Tab", "Alt+E", "Alt+M"):
        assert k in bar, f"helper bar lost {k}"


def test_tab_and_arrow_handlers_exist():
    assert 'e.key === "Tab"' in HTML and "window.vizierTab" in HTML
    assert "window.vizierPart" in HTML and "ArrowDown" in HTML and "ArrowUp" in HTML


def test_mode_tabs_explain_themselves():
    for i in ("tab-talk", "tab-rec", "tab-call"):
        assert re.search(rf'id="{i}" title="[A-Z]+: ', HTML), f"{i} lost its explanation tooltip"
