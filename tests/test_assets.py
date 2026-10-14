import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
TEMPLATES = STATIC.parent / "templates"
NAMES = "search globe inbox users database shield clock file check building alert sun moon".split()


def test_every_font_face_file_exists_and_licence_ships():
    urls = re.findall(r"@font-face\{[^}]*url\(([^)]+)\)", (STATIC / "portal.css").read_text())
    assert len(urls) == 4
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
    block = css[css.index("@media (pointer:coarse)"):]
    assert "min-height:44px" in block
    for sel in (".btn", ".btn-sm", "details.mini>summary", ".site-nav a", "select"):
        assert sel in block.split("{")[1].split(","), sel
