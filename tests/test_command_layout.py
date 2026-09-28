"""The minimal Command screen keeps every feature: the five readouts (same ids
the dashboard fills), the eleven orbit buttons, the eleven planets, the Ask bar
and the live feed — and its text stays readable on every theme."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "dashboard/static"


def _command_html():
    html = (STATIC / "index.html").read_text()
    start = html.index('<section id="tab-overview"')
    return html[start:html.index("</section>", start)]


def test_every_readout_and_control_is_still_on_the_command_screen():
    cmd = _command_html()
    stats = cmd[cmd.index('class="cmd-stats"'):cmd.index('class="command-stage"')]
    for rid in ("hud-cost", "hud-cost-meta", "hud-calls", "hud-calls-meta", "hud-cache", "hud-cache-bar",
                "hud-evo", "hud-evo-meta", "hud-evo-frame", "hud-model", "hud-systems"):
        assert f'id="{rid}"' in stats, rid
    for part in ('id="globe"', 'id="feature-orbit"', 'id="planet-selector"', 'id="cst-quickask"',
                 'id="cmd-ticker"', 'id="cmd-clock"', 'id="cmd-starfield"'):
        assert part in cmd, part


def test_all_orbit_buttons_and_planets_are_kept():
    js = (STATIC / "app.js").read_text()
    features = js[js.index("const FEATURES = ["):js.index("];", js.index("const FEATURES = ["))]
    assert len(re.findall(r"\{ tab: '", features)) == 11
    bodies = js[js.index("const SOLAR_BODIES = {"):js.index("};", js.index("const SOLAR_BODIES = {"))]
    assert len(re.findall(r"^\s+\w+:\s+\{ label:", bodies, re.M)) == 11


def test_command_text_does_not_use_theme_text_colours():
    """The Command background is always dark; Daylight's dark text vanished on it."""
    css = (STATIC / "styles.css").read_text()
    section = css[css.index("/* --- Command, minimal:"):css.index("/* --- Sidebar brand polish --- */")]
    assert "--cmd-text" in section and not re.search(r"var\(--text(-mute|-dim)?\)", section)
    ask = css[css.index(".cst-quickask input {"):css.index(".cst-quickask button {")]
    assert "var(--cmd-text)" in ask and "var(--muted)" not in ask
