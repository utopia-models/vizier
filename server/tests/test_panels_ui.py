"""the deployment's issue 13798 (the user's cards 2026-11-15 2:09 PM): "Tab cycles four Vizier panels: Live transcript, Settings (voice
picker, ears/mouth, wake rules), Desk picker, Agent status." Static checks on the app page: the four panels exist, Tab
cycles exactly them, the global arrows reach a panel, and every setting row is changeable from the keyboard."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "app" / "ui" / "index.html").read_text()


def test_four_panels_and_their_tabs_exist():
    for k in ("talk", "settings", "desks", "agents", "tscript", "hscript"):
        assert f'id="tab-{k}"' in HTML and f'id="m-{k}"' in HTML, k


def test_tab_cycles_the_four_panels():
    fn = HTML[HTML.index("window.vizierTab = () =>"):]
    fn = fn[: fn.index("};")]
    assert 'const order = ["talk", ...PANELS]' in fn   # Tab reaches SCRIPT (#14)
    assert re.search(r'const PANELS = \["settings", "desks", "agents", "tscript", "hscript"\]', HTML)


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
