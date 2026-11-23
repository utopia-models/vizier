"""issue 14087 (S6, readable prompter): a CDP read of the live window on SCRIPT finds type of 28 px or less and no
outline label ("Coach on part", "BUILD") anywhere in the DOM. Hiding them with CSS left them in the DOM (2026-11-16 headless
read of main: 1 "Coach on part", 3 "BUILD"/"Build" hits on SCRIPT); they are emptied on SCRIPT and refilled on CONTEXT."""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[2] / "app" / "ui" / "index.html").read_text()


def test_type_ceiling_is_28px():
    assert re.search(r"const TYPE_MAX = 28;", HTML)
    assert "Math.min(TYPE_MAX, +store.get(\"vizier.type2\")" in HTML, "a stored larger size must be clamped at load"
    assert "Math.min(TYPE_MAX, fontPx + d)" in HTML, "Ctrl+= must stop at the ceiling"
    assert not re.search(r"Math\.min\(40,", HTML)


def test_script_view_empties_coach_build_and_context():
    assert 'const onScript = () => mode === "rec" && rtab === "script";' in HTML
    clear = re.search(r"function clearForScript\(\) \{(.*?)\}\n", HTML).group(1)
    for el in ('$("coach").innerHTML = ""', '$("build").innerHTML = ""', '$("doc").innerHTML = ""'):
        assert el in clear, f"clearForScript must empty {el}"
    coach = re.search(r"async function coach\(\) \{(.*?)\n\}", HTML, re.S).group(1)
    assert coach.index("if (onScript())") < coach.index("Coach on part"), "coach() writes the label only off SCRIPT"
    build = re.search(r"async function buildlog\(\) \{(.*?)\n\}", HTML, re.S).group(1)
    guard = 'if (!(mode === "rec" && rtab === "context")) { $("build").innerHTML = "";'
    assert guard in build, "buildlog() empties the footer unless CONTEXT is showing (vizier#76: no stale BUILD line under the script)"
    assert build.index(guard) < build.index("const shown"), "the guard runs before any build line is written"
    assert 'if (tb === "script" && mode === "rec") clearForScript();' in HTML
