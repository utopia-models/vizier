"""Replay test (the user 2026-11-14): the Script / Context tabs, Tab to switch, arrows to step and the helper bar must not
disappear again. TALK became the default mode and hid all of them; the default is the SCRIPT view now."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "app" / "ui" / "index.html").read_text()


def test_default_mode_is_the_script_view():
    m = re.search(r"function autoMode\(\) \{ return (.*?); \}", HTML)
    assert m, "autoMode() missing"
    assert m.group(1).strip() == '"rec"', "the default is the SCRIPT view (no CALL mode, TALK only when picked)"


def test_script_and_context_tabs_exist():
    assert 'data-t="script"' in HTML and 'data-t="context"' in HTML


def test_helper_bar_lists_the_keys():
    bar = re.search(r'<div id="keylegend">(.*?)</div>', HTML, re.S).group(1)
    for k in ("↑ ↓", "← →", "Tab", "Alt+E", "Alt+M"):
        assert k in bar, f"helper bar lost {k}"


def test_tab_and_arrow_handlers_exist():
    assert 'k === "Tab"' in HTML and "window.vizierTab" in HTML
    assert "window.vizierPart" in HTML and "ArrowDown" in HTML and "ArrowUp" in HTML


def test_one_top_row_talk_script_context():
    """the user 2026-11-14: one top bar, no second SCRIPT row, no Do not say; CALL appears only while a call is live.
    Superseded in part by his cards of 2026-11-15 2:09 PM (the deployment's issue 13798): the row also carries SETTINGS / DESKS /
    AGENTS and Tab cycles TALK / SETTINGS / DESKS / AGENTS (test_panels_ui.py); SCRIPT and CONTEXT stay in the row."""
    for i, label in (("tab-talk", "TALK"), ("tab-rec", "SCRIPT"), ("tab-ctx", "CONTEXT")):
        assert re.search(rf'id="{i}"[^>]*>{label}</span>', HTML), f"top bar lost {label}"
    assert re.search(r"#rtabs \{ display:none !important; \}", HTML), "the second tab row must stay hidden"


def test_context_tab_is_live_activity():
    assert "WHAT SCOUT IS DOING" in HTML and "buildlog?n=30" in HTML
