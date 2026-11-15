"""Replay test (2026-11-15, asked twice): "I want the option to be able to minimize Vizier" (Alt+V; Alt+V Alt+V until #13798), and
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


def test_one_alt_v_pops_vizier_up_or_hides_it():
    """the deployment's issue 13798 (the user's card 2026-11-15 2:09 PM): "Alt+V pops Vizier bottom-left, small". One press, not two."""
    assert "park::alt_v(" not in MAIN, "Alt+V is a single press now (it was Alt+V Alt+V)"
    assert ".minimize()" in MAIN, "nothing ever hides the window"
    restore = MAIN[MAIN.index("park::Toggle::Restore =>"):]
    restore = restore[: restore.index("}")]
    assert "place(&w)" in restore and "set_focus" in restore, "Alt+V must put Vizier back in its slot and focus it"


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
    assert "<b>Alt+V</b> show / hide" in bar
