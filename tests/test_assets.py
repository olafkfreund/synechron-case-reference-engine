import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
TEMPLATES = STATIC.parent / "templates"
NAMES = "search globe inbox users database shield clock file check building alert sun moon".split()


def test_every_font_face_file_exists_and_licence_ships():
    urls = re.findall(r"@font-face\{[^}]*url\(([^)]+)\)", (STATIC / "portal.css").read_text())
    assert len(urls) == 7 and len([x for x in urls if "-ext-" in x]) == 3  # 3 latin, 3 latin-ext, Plex Mono
    assert all((STATIC / u).is_file() for u in urls)
    assert "Montserrat" in (STATIC / "fonts" / "OFL.txt").read_text()


def test_every_icon_renders_an_svg():
    t = Environment(loader=FileSystemLoader(TEMPLATES)).from_string('{% from "_icons.html" import icon %}{{ icon(n) }}')
    for n in NAMES:
        out = t.render(n=n)
        assert out.count("<svg") == 1 and 'stroke-width="1.5"' in out, n
        assert re.search(r"<(path|circle|ellipse)", out), n


def test_coarse_pointer_keeps_44px_targets():
    css = (STATIC / "portal.css").read_text()
    block = css[css.index("@media (any-pointer:coarse)"):]
    assert "min-height:44px" in block
    for sel in (".btn", ".btn-sm", "details.mini>summary", ".site-nav a", "select"):
        assert sel in block.split("{")[1].split(","), sel


def test_open_mini_panel_is_a_plain_subtle_surface_and_the_badge_stays_by_the_name():
    css = (STATIC / "portal.css").read_text()
    panel = re.search(r"details\.mini\[open\]\{([^}]*)\}", css).group(1)
    assert "var(--subtle)" in panel and "var(--border)" in panel and "blur" not in css
    assert re.search(r"\n\.src \.who\{display:flex", css)


def test_badge_height_is_a_minimum_so_long_notices_wrap():
    css = (STATIC / "portal.css").read_text()
    rule = re.search(r"\n\.badge\{([^}]*)\}", css).group(1)
    assert "min-height:20px" in rule and not re.search(r"(?<!-)height:20px", rule)


def test_hugeicons_licence_ships_and_is_referenced():
    assert "Hugeicons" in (TEMPLATES / "LICENSE-hugeicons.txt").read_text()
    assert "LICENSE-hugeicons.txt" in (TEMPLATES / "_icons.html").read_text()


def test_touch_block_covers_file_inputs_and_drops_the_overlays_and_panel_has_padding():
    css = (STATIC / "portal.css").read_text()
    block = css[css.index("@media (any-pointer:coarse)"):]
    assert "input[type=file]" in block and ".src-add>summary{min-height:44px" in block
    assert re.search(r"\.btn-sm:not\(summary\)::after,[^{]*\.src-add>summary::after\{content:none\}", block)
    assert re.search(r"details\.mini\[open\]\{[^}]*padding:8px 12px 12px", css)
    assert "36px buttons" not in css
