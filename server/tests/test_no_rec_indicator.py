"""knowledge#14552 (Tyler 2026-11-17: "less is more"): Vizier shows no recording indicator. OBS's red border is the
signal on the laptop; the SETTINGS "Recording" row (a control, Alt+R's twin) is the only place the take's state is
written."""
import re
from pathlib import Path

UI = (Path(__file__).resolve().parents[2] / "app/ui/index.html").read_text()


def test_no_rec_pill_and_no_recording_mark():
    assert 'id="vsl"' not in UI and "#vsl" not in UI
    assert "● REC" not in UI
    assert "● Recording ·" not in UI


def test_the_settings_row_still_drives_the_take():
    assert re.search(r'k: "vsl", label: "Recording \(VSL take\)"', UI)
    assert "window.vizierRecToggle" in UI and "window.vizierRecLocal" in UI and "window.vizierRecServer" in UI
