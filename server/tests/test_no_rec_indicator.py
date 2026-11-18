"""issue 14552 (the user 2026-11-17: "less is more"): Vizier shows no recording indicator. OBS's red border is the
signal on the laptop; the SETTINGS "Recording" row (a control, Alt+R's twin) is the only place the take's state is
written."""
from pathlib import Path

UI = (Path(__file__).resolve().parents[2] / "app/ui/index.html").read_text()


def test_no_rec_pill_and_no_recording_mark():
    assert 'id="vsl"' not in UI and "#vsl" not in UI
    assert "● REC" not in UI
    assert "● Recording ·" not in UI


def test_no_recording_control():
    assert 'label: "Recording' not in UI and "Alt+R" not in UI   # the button is gone
