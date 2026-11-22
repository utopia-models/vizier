"""the deployment's issue 13798 (the user's cards 2026-11-15 2:09 PM): "Tab cycles four Vizier panels: Live transcript, Settings (voice
picker, ears/mouth, wake rules), Desk picker, Agent status." Static checks on the app page: the four panels exist, Tab
cycles exactly them, the global arrows reach a panel, and every setting row is changeable from the keyboard."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "app" / "ui" / "index.html").read_text()


def test_four_panels_and_their_tabs_exist():
    for k in ("talk", "settings", "tscript", "hscript", "qc", "approval"):
        assert f'id="tab-{k}"' in HTML and f'id="m-{k}"' in HTML, k


def test_tab_cycles_the_three_panels_and_qc_approval_are_gone():
    """The user 2026-11-22: QC and APPROVAL "fixed or removed": removed. Tab cycles TALK, SETTINGS, Speaker Script, Eagle Script."""
    fn = HTML[HTML.index("window.vizierTab = () =>"):]
    fn = fn[: fn.index("};")]
    assert '["talk", ...PANELS]' in fn   # Tab reaches SCRIPT (#14)
    assert re.search(r'const PANELS = \["settings", "tscript", "hscript"\]', HTML)
    for k in ("qc", "approval"):
        assert re.search(rf'<span id="tab-{k}" hidden></span>', HTML), k     # an inert stub, never a visible tab
        assert f'class="tab hs" id="tab-{k}"' not in HTML, k


def test_settings_shows_the_live_transcript():
    """The user 2026-11-22: the live transcript sits under SETTINGS, fed by the same GET /talk/transcript as TALK."""
    sec = HTML[HTML.index('<section class="mode" id="m-settings">'):]
    sec = sec[: sec.index("</section>")]
    assert 'id="settalk"' in sec
    poll = HTML[HTML.index("async function pollTalk()"):]
    poll = poll[: poll.index("\n}\n")]
    assert 'mode === "settings" ? "settalk"' in poll and "talk/transcript?since=" in poll


def test_down_into_ai_voice_hands_off():
    """The user 2026-11-22: Down onto the first [AI voice] line starts the voice agent (POST /handoff) and swaps face -> orb
    (studiod "face orb"); only Down, only across the boundary, from panelStep (his keys), never from anything else."""
    step = HTML[HTML.index("function panelStep(d) {"):]
    step = step[: step.index("\n}\n")]
    assert "maybeHandoff(was, tsCur)" in step
    fn = HTML[HTML.index("function maybeHandoff(from, to) {"):]
    fn = fn[: fn.index("\n}\n")]
    assert "to <= from" in fn and 'roleOf(b.title).row !== "ai"' in fn and 'roleOf(a.title).row === "ai"' in fn
    assert 'api("handoff"' in fn and '"face orb"' in fn
    assert HTML.count("maybeHandoff(") == 2      # defined once, called once: nothing but his Down key fires it


def test_no_desks_or_agents_panel_and_every_tab_has_the_job_header():
    """Tyler 2026-11-22: DESKS and AGENTS are gone for now; the job header sits above every tab; the pages the voice agent
    shows get a tab each; the speaker script styles by who says it."""
    for k in ("desks", "agents"):
        assert f'id="tab-{k}"' not in HTML and f'id="m-{k}"' not in HTML, k
    assert HTML.index('id="jobhdr"') < HTML.index('id="m-talk"') and "async function loadHdr" in HTML
    assert 'roleOf(s.title)' in HTML and "[Tyler]" in HTML and "[AI voice]" in HTML and "HANDOFF:" in HTML


def test_global_arrows_drive_the_panels():
    step = HTML[HTML.index("window.vizierStep = d =>"):][:200]
    part = HTML[HTML.index("window.vizierPart = d =>"):][:200]
    assert "panelStep(d)" in step and "panelChange(d)" in part


def test_every_setting_row_changes_by_keyboard():
    rows = re.findall(r'\{ k: "(\w+)", label: "[^"]+",[^\n]*?(ro: true)?\s*\},?\n', HTML[HTML.index("function settingRows"):HTML.index("function drawSettings")])
    editable = {k for k, ro in rows if not ro}
    assert {"voice", "ears", "mouth", "wake_on", "wake_call"} <= editable, rows
    change = HTML[HTML.index("async function changeSetting"):HTML.index("function drawDesks")]
    for k in ("voice", "ears", "wake_on", "wake_call"):
        assert f'"{k}"' in change, f"{k} has no keyboard change"


def test_ears_mouth_answer_before_the_server_does():
    """A press shows its result in the same frame; the server's answer only corrects it."""
    talk = (Path(__file__).resolve().parents[2] / "app" / "ui" / "talk.js").read_text()
    t = talk[talk.index("export async function toggle"):][:700]
    assert t.index("pending[k] = true") < t.index("paint();") < t.index("await applyEars")


def test_vizier_has_no_diagram_or_app_tab_and_no_user_visible_mouth():
    """Tyler 2026-11-22: the architecture diagram and the working app are plain Chrome tabs the take driver opens, not Vizier
    tabs; and the word on screen is Voice, never Mouth."""
    low = HTML.lower()
    assert "pvframe" not in low and "tab-pv" not in low and "<iframe" not in low and "preview" not in low
    assert "Mouth" not in HTML and "mouth</b>" not in HTML
    talk = (Path(__file__).resolve().parents[2] / "app" / "ui" / "talk.js").read_text()
    assert ">Voice <b>" in talk and "Mouth" not in talk.replace("applyMouth", "")


def _sections(md):
    import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import vizier
    return vizier.spoken_sections(md)


INTRO_ONLY = """# Upwork Intro VSL \u00b7 Take 1

## [Tyler] Intro
Hi, I'm Tyler Youk.
I'm looking at your AI Automation Architect post.
I have an architecture diagram and an MVP ready.

## HANDOFF: the voice agent takes over
Stay silent, the voice agent takes over.

## [AI voice] Architecture diagram
Every morning a routine wakes the chief of staff.
It reads your priorities from memory.

## [AI voice] Working app
Here's one morning briefing, box by box.
"""
ENTIRE = """# Upwork Full VSL \u00b7 Take 1

## [Tyler] Your post
I read your whole post.
You want one chief of staff.

## [Tyler] The demo
Here is the demo.
"""


def _no_paragraphs(secs):
    for s in secs:
        for l in s["lines"]:
            assert len(l.split()) <= 15, ("a paragraph, not a line", l)
            assert not re.search(r'[.!?]["\u201d]?\s+[A-Z]', l), ("two sentences in one row", l)


def test_intro_only_script_is_tagged_sections_in_order_and_one_line_per_row():
    secs = _sections(INTRO_ONLY)
    assert [s["title"] for s in secs] == ["[Tyler] Intro", "HANDOFF: the voice agent takes over", "[AI voice] Architecture diagram", "[AI voice] Working app"]
    assert [len(s["lines"]) for s in secs] == [3, 1, 2, 1]
    _no_paragraphs(secs)


def test_entire_script_is_all_tyler_sections_no_handoff_no_ai():
    secs = _sections(ENTIRE)
    assert [s["title"] for s in secs] == ["[Tyler] Your post", "[Tyler] The demo"]
    _no_paragraphs(secs)


def test_a_paragraph_row_would_fail_the_check():
    import pytest
    with pytest.raises(AssertionError):
        _no_paragraphs(_sections("## [Tyler] Intro\nHi, I'm Tyler Youk. I'm looking at your post. I have a diagram ready for you and an MVP too.\n"))


def test_current_line_indented_and_highlighted_next_dimmed_finished_faded():
    """Tyler 2026-11-22: copied from the prompter he approved (#text p dim, p.cur lit): the line he is on is indented + highlighted
    with the arrow, the next line is dimmed, finished lines are faded; sections carry [Tyler] / [AI voice] tags only."""
    fn = HTML[HTML.index("function drawTscript"):]; fn = fn[: fn.index("\n}\n")]
    for cls in ("cur", "done", "next", "later"):
        assert f'" {cls}"' in fn or f'" sel {cls}"' in fn, cls
    assert 'cur ? "\u25b6" : ""' in fn and "s.lines.map(" in fn
    css = lambda sel: re.search(re.escape(sel) + r"\s*\{([^}]*)\}", HTML).group(1)
    assert "margin-left" in css(".panel .row.cur") and "background" in css(".panel .row.cur") and "opacity:1" in css(".panel .row.cur")
    assert "color:var(--hi)" in css(".panel .row.cur .v")
    done, nxt = float(re.search(r"opacity:([\d.]+)", css(".panel .row.done")).group(1)), float(re.search(r"opacity:([\d.]+)", css(".panel .row.next")).group(1))
    assert done < nxt < 1
    for old in ("YOU SAY", "AI VOICE SAYS", "INTRO (Tyler)", "AI VOICE (do not read)"):
        assert old not in HTML, old
