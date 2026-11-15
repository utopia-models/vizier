"""Replay test (2026-11-15, asked twice): "I want the option to be able to minimize Vizier" with Alt+V Alt+V, and
"I don't want a rule or anything saying like Vizier only comes up during VSLs". The window keeper un-minimized Vizier every
2 s, so a minimize never stuck. Alt+V Alt+V parks it; the keeper must consult the park before restoring."""
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
MAIN = (APP / "src" / "main.rs").read_text()
HTML = (APP / "ui" / "index.html").read_text()


def test_alt_v_is_a_global_shortcut():
    assert re.search(r"Shortcut::new\(Some\(Modifiers::ALT\), Code::KeyV\)", MAIN), "Alt+V is not registered"
    m = re.search(r"\.with_shortcuts\(\[(.*?)\]\)", MAIN, re.S)
    assert m and "vizier" in m.group(1), "Alt+V is defined but not in with_shortcuts"


def test_alt_v_needs_a_double_press_and_minimizes():
    assert "park::alt_v(" in MAIN, "a single Alt+V must not minimize: Alt+V Alt+V"
    assert ".minimize()" in MAIN, "nothing ever minimizes the window"


def test_keeper_leaves_a_parked_window_alone():
    loop = MAIN[MAIN.index("std::thread::spawn(move || loop {"):]
    loop = loop[: loop.index("place(&keep);")]
    assert "park::keep(" in loop, "the 2 s keeper restores a minimized window without asking park.rs"
    assert loop.index("park::keep(") < loop.index(".unminimize()"), "the keeper un-minimizes before consulting the park"
    assert loop.index("set_content_protected") < loop.index("park::keep("), "content protection must hold while parked"


def test_no_visibility_rule():
    """The ability, not a rule: nothing hides Vizier on its own (no VSL/recording gate on showing the window)."""
    assert "w.show()?;" in MAIN, "Vizier must still open on screen"
    assert not re.search(r"(?i)(vsl|recording)[^\n]*\.(hide|minimize)\(", MAIN)


def test_helper_bar_lists_alt_v():
    bar = re.search(r'<div id="keylegend">(.*?)</div>', HTML, re.S).group(1)
    assert "Alt+V Alt+V" in bar
