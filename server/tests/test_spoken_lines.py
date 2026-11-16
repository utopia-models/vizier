"""Replay test (the user 2026-11-16 3:13 AM MT, recording a custom VSL from an outline, vizier#15): "I'm barely able to read
the script. The text is extremely large... literally taking up half the screen... there's this section called coach on
part... build." The SCRIPT tab shows the spoken lines and nothing else, at a type size that fits several lines."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vizier  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "app" / "ui" / "index.html").read_text()

OUTLINE = """# Custom VSL for a members' site
## 1 · Their job
### Hook
> Coach: warm, slow, look at the lens
Hi, it's me.
[show] the OCC
- I read your post: you need someone to keep your members' site running.
(pause)
Coach on part 1: land the problem before the offer.
---
**A site people rely on can't go down while it gets new features.**
## 2 · What I'd do
Note: this is the build section
1. Steady upkeep of the site and the member directory.
(scout) Where AI helps, it gets added without breaking what works.
## Do not say
the app name
"""


def test_only_spoken_lines_survive():
    takes = vizier.parse_takes(OUTLINE)
    assert [k["title"] for k in takes] == ["1 · Their job", "2 · What I'd do"]   # "Do not say" is a note, not a part
    assert takes[0]["lines"] == ["Hi, it's me.",
                                 "I read your post: you need someone to keep your members' site running.",
                                 "A site people rely on can't go down while it gets new features."]
    assert takes[1]["lines"] == ["Steady upkeep of the site and the member directory.",
                                 "(scout) Where AI helps, it gets added without breaking what works."]


def test_a_plain_script_is_unchanged():
    plain = "## 1 · Hello\nthe current script\nsecond breath\n"
    assert vizier.parse_takes(plain) == [{"title": "1 · Hello", "lines": ["the current script", "second breath"]}]


def test_the_app_applies_the_same_rule():
    m = re.search(r"^const NOT_SPOKEN = /(.+)/i;$", HTML, re.M)
    assert m, "index.html lost NOT_SPOKEN"
    assert m.group(1) == vizier.NOT_SPOKEN.pattern, "the app and the server must drop the same lines"
    assert "const lines = spoken(k.lines)" in HTML and 'lines: lines.length, text: (lines[curIdx] || "")' in HTML
    assert 'p class="cue"' not in HTML, "a cue paragraph must never reach the DOM (the arrows would step onto it)"


def test_script_tab_hides_coach_build_and_heading():
    assert re.search(r'body\[data-mode="rec"\] #coach, body\[data-mode="rec"\] #build, body\[data-mode="rec"\] #title', HTML)


def test_type_size_default_and_ceiling():
    assert re.search(r'let fontPx = Math\.min\(40, \+store\.get\("vizier\.type2"\) \|\| 22\);', HTML)
    assert '"vizier.type"' not in HTML, "the old key would bring the oversized stored size back"


def test_a_part_built_while_hidden_is_built_again():
    # the smoke test caught it 2026-11-16: the takes arrived before the first /health set the mode, mark() ran on a
    # display:none prompter (every offsetTop 0) and the LAST line was current with the scroll at the top
    assert 'if (!$("text").clientHeight) built = "";' in HTML
    assert 'if (tb === "script") render();' in HTML


def test_header_and_legend_are_one_row():
    assert "#spd { font:600 12px ui-monospace, monospace; color:var(--dim); white-space:nowrap; }" in HTML
    assert re.search(r"#keylegend \{[^}]*white-space:nowrap; overflow:hidden; text-overflow:ellipsis;", HTML)
    assert "#hide { display:none; }" in HTML
