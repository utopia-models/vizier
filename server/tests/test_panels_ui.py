"""the deployment's issue 13798 (the user's cards 2026-11-15 2:09 PM): "Tab cycles four Vizier panels: Live transcript, Settings (voice
picker, ears/mouth, wake rules), Desk picker, Agent status." Static checks on the app page: the four panels exist, Tab
cycles exactly them, the global arrows reach a panel, and every setting row is changeable from the keyboard."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "app" / "ui" / "index.html").read_text()


def test_four_panels_and_their_tabs_exist():
    for k in ("talk", "settings", "tscript", "hscript", "pv0", "pv1", "qc", "approval"):
        assert f'id="tab-{k}"' in HTML and f'id="m-{k}"' in HTML, k


def test_tab_cycles_the_four_panels():
    fn = HTML[HTML.index("window.vizierTab = () =>"):]
    fn = fn[: fn.index("};")]
    assert '["talk", ...PANELS]' in fn   # Tab reaches SCRIPT (#14); hidden preview tabs are skipped
    assert re.search(r'const PANELS = \["settings", "tscript", "hscript", "pv0", "pv1", "qc", "approval"\]', HTML)


def test_no_desks_or_agents_panel_and_every_tab_has_the_job_header():
    """Tyler 2026-11-22: DESKS and AGENTS are gone for now; the job header sits above every tab; the pages the voice agent
    shows get a tab each; the speaker script styles by who says it."""
    for k in ("desks", "agents"):
        assert f'id="tab-{k}"' not in HTML and f'id="m-{k}"' not in HTML, k
    assert HTML.index('id="jobhdr"') < HTML.index('id="m-talk"') and "async function loadHdr" in HTML
    assert 'roleOf(s.title)' in HTML and "YOU SAY:" in HTML and "AI VOICE SAYS" in HTML and "HANDOFF:" in HTML


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


def test_follow_and_ears_mouth_answer_before_the_server_does():
    """A press shows its result in the same frame; the server's answer only corrects it."""
    fn = HTML[HTML.index("window.vizierFollow = async"):][:600]
    assert fn.index("paintFollow(!followOn)") < fn.index('api("prompter/follow"')
    talk = (Path(__file__).resolve().parents[2] / "app" / "ui" / "talk.js").read_text()
    t = talk[talk.index("export async function toggle"):][:700]
    assert t.index("pending[k] = true") < t.index("paint();") < t.index("await applyEars")
