"""knowledge#14552 then knowledge#14720 (Tyler 2026-11-17 06:37Z: "I still see a red record button. It's not that I don't
want to see the button. I don't want that functionality at all."): Vizier has no recording feature. No REC pill, no
Recording row, no Alt+R, no record handler in the app, the page or the server. OBS is the only recorder."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {p: p.read_text() for p in [ROOT / "app/ui/index.html", ROOT / "app/ui/talk.js", ROOT / "app/src/main.rs",
                                       ROOT / "server/vizier.py", ROOT / "server/prompter.html"]}
HANDLERS = re.compile(r"prompter_record|prompter/record|rec_toggle|vizierRec|vslLabel|vslFromServer|\"/vsl\"|KeyR|alt\+r|"
                      r"Alt\+R|async function record\(|\brecord\(true\)|VIZIER_VSL_|VIZIER_CAM\b")


def test_no_record_handler_anywhere():
    hits = [f"{p.relative_to(ROOT)}: {m.group(0)}" for p, s in SOURCES.items() for m in HANDLERS.finditer(s)]
    assert hits == []


def test_no_rec_pill_and_no_recording_mark():
    ui = SOURCES[ROOT / "app/ui/index.html"]
    assert 'id="vsl"' not in ui and "#vsl" not in ui and 'id="rec"' not in ui
    assert "● REC" not in ui and "● Recording" not in ui and "Recording (VSL take)" not in ui
