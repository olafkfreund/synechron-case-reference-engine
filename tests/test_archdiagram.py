import pytest

from app.archdiagram import H, W, layout, measure, wrap
from app.schema import Architecture

ZONES = ["channels", "experience", "integration", "core", "data", "external", "platform"]
KINDS = ["user", "app", "service", "queue", "store", "external", "platform"]


def arch(zones, n_flows=0, role="Handles the payment rail and settles every batch overnight, retried", name="Payments hub"):
    """One component per entry in `zones`, flows chained and then spread across the diagram."""
    comps = [{"id": f"c{i}", "name": f"{name} {i}", "role": role, "zone": z, "kind": KINDS[i % 7]} for i, z in enumerate(zones)]
    modes = ["api", "event", "batch", "file", "ui", "data"]
    flows = [{"src": f"c{i % len(zones)}", "dst": f"c{(i * 5 + 3) % len(zones)}", "label": f"label {i}", "mode": modes[i % 6]}
             for i in range(n_flows) if i % len(zones) != (i * 5 + 3) % len(zones)]
    return Architecture.model_validate({"components": comps, "flows": flows,
                                        "callouts": [{"text": "Cut onboarding from 12 days to 2", "outcome": 0}]})


FULL = ["channels", "channels", "experience", "integration", "integration", "core", "core", "core", "data", "data", "external", "platform"]
CASES = {
    "full": arch(FULL, 14),
    "one-zone": arch(["core"] * 12, 14),
    "two-zones": arch(["channels"] * 6 + ["external"] * 6, 14),
    "platform-only": arch(["platform"] * 12, 6),
    "six-columns": arch(["channels", "experience", "integration", "core", "data", "external"] * 2, 14),
    "tiny": arch(["core"]),
}


def cards(sc):
    return [b for b in sc.boxes if b.style.startswith("card-")]


def test_same_input_same_scene():
    assert layout(CASES["full"], "Source: Demo · reviewed 2026-01-01") == layout(CASES["full"], "Source: Demo · reviewed 2026-01-01")


@pytest.mark.parametrize("name", CASES)
def test_cards_do_not_overlap_and_everything_is_inside_the_canvas(name):
    sc = layout(CASES[name], "Source: Demo")
    assert len(cards(sc)) == len(CASES[name].components)  # 12 components and 14 flows still fit
    cs = cards(sc)
    for n, a in enumerate(cs):
        for b in cs[n + 1:]:
            assert a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y, (a.ref, b.ref)
    for b in [*sc.zones, *sc.boxes]:
        assert 0 <= b.x and b.x + b.w <= W and 0 <= b.y and b.y + b.h <= H, b
    for ln in sc.lines:
        assert all(0 <= x <= W and 0 <= y <= H for x, y in ln.points), ln
    for t in sc.texts:
        assert 0 <= t.x <= W and 0 <= t.y <= H, t


@pytest.mark.parametrize("name", CASES)
def test_text_stays_inside_its_card_and_pills_clear_the_cards(name):
    sc = layout(CASES[name])
    for c in cards(sc):
        for t in sc.texts:
            if c.x + 40 <= t.x <= c.x + c.w and c.y <= t.y <= c.y + c.h and t.style in ("name", "role"):
                assert t.x + measure(t.text, t.style) <= c.x + c.w - 4, t
    for p in (b for b in sc.boxes if b.style.startswith("pill-")):
        for c in cards(sc):
            assert p.x + p.w <= c.x or c.x + c.w <= p.x or p.y + p.h <= c.y or c.y + c.h <= p.y, (p, c.ref)


def test_empty_zones_collapse_and_widths_follow_the_zones_in_use():
    two = layout(arch(["channels", "core"]))
    five = layout(arch(["channels", "experience", "integration", "core", "data"]))
    assert len(two.zones) == 2 and len(five.zones) == 5
    assert two.zones[0].w > five.zones[0].w or cards(two)[0].w >= cards(five)[0].w
    assert cards(two)[0].w == 230 and all(z.style == "zone" for z in two.zones)
    assert not any(t.text == "PLATFORM" for t in two.texts)


def test_text_wraps_to_two_lines_with_an_ellipsis():
    lines = wrap("one two three four five six seven eight nine ten eleven twelve", "role", 120, 2)
    assert len(lines) == 2 and lines[1].endswith("…") and all(measure(x, "role") <= 120 for x in lines)
    assert wrap("short", "role", 120, 2) == ["short"]
    sc = layout(arch(["core"], name="Core banking ledger and posting engine"))
    assert sum(t.style == "name" for t in sc.texts) == 2 and sum(t.style == "role" for t in sc.texts) == 1


def test_barycentre_puts_connected_cards_level():
    a = Architecture.model_validate({"components": [
        {"id": "x1", "name": "X1", "zone": "channels", "kind": "app"}, {"id": "x2", "name": "X2", "zone": "channels", "kind": "app"},
        {"id": "y1", "name": "Y1", "zone": "core", "kind": "app"}, {"id": "y2", "name": "Y2", "zone": "core", "kind": "app"}],
        "flows": [{"src": "x1", "dst": "y2", "mode": "api"}, {"src": "x2", "dst": "y1", "mode": "api"}]})
    by = {b.ref: b for b in cards(layout(a))}
    assert (by["x1"].y < by["x2"].y) == (by["y2"].y < by["y1"].y) or by["x1"].y == by["y2"].y


# --- renderers ---------------------------------------------------------------------------------
import io
import json
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image
from pptx import Presentation
from pptx.util import Inches

from app.archdiagram import to_mermaid, to_png, to_pptx_shapes, to_svg

FIX = Path(__file__).parent / "fixtures" / "arch"
SAMPLES = {p.stem: Architecture.model_validate_json(p.read_text()) for p in sorted(FIX.glob("*.json"))}


def test_sample_sizes():
    assert {k: (len(a.components), len(a.flows)) for k, a in SAMPLES.items()} == {"small": (5, 5), "typical": (9, 10), "max": (12, 14)}


def test_svg_parses_and_is_inert():
    a = Architecture.model_validate({"title": "Pay <&> \"go\"", "components": [{"id": "a", "name": "A<script>", "zone": "core", "kind": "app"}]})
    svg = to_svg(layout(a, "Source: x & y"), a.title)
    root = ElementTree.fromstring(svg)
    assert root.get("viewBox") == "0 0 1600 900" and root.get("width") is None
    low = svg.lower()
    assert "<script" not in low and "href" not in low and "http" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "A&lt;script&gt;" in svg


@pytest.mark.parametrize("name", SAMPLES)
def test_png_is_2x_and_text_is_drawn_in_every_card(name):
    sc = layout(SAMPLES[name], "Source: Demo")
    im = Image.open(io.BytesIO(to_png(sc))).convert("RGB")
    assert im.size == (3200, 1800)
    for c in cards(sc):
        box = im.crop((int(c.x * 2) + 6, int(c.y * 2) + 6, int((c.x + c.w) * 2) - 6, int((c.y + c.h) * 2) - 6))
        dark = sum(1 for p in box.getdata() if sum(p) < 450)
        assert dark / (box.width * box.height) > 0.01, c.ref


@pytest.mark.parametrize("name", SAMPLES)
def test_pptx_shapes_one_freeform_per_flow_and_text_reads_back(name):
    a = SAMPLES[name]
    sc = layout(a, "Source: Demo")
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    to_pptx_shapes(slide, sc, Inches(.4), Inches(.3), Inches(9), Inches(5))
    shapes = list(slide.shapes)
    assert sum(s.name.startswith("arch:card-") for s in shapes) == len(a.components)
    assert sum(s.name.startswith("arch:flow-") for s in shapes) == len(a.flows) + len({f.mode for f in a.flows})  # plus the legend samples
    assert sum(s.name.startswith(("arch:zone", "arch:card-")) for s in shapes) >= len(a.components) + len(sc.zones)
    text = " ".join(s.text_frame.text for s in shapes if s.has_text_frame)
    assert all(c.name.split()[0] in text for c in a.components) and a.title.split()[0] in text
    assert "tailEnd" in slide.shapes._spTree.xml


def test_mermaid_snapshot_and_escaping():
    assert to_mermaid(SAMPLES["small"]) == (FIX / "small.mmd").read_text()
    a = Architecture.model_validate({"components": [
        {"id": "a b", "name": 'Say "hi" [x]', "zone": "core", "kind": "app"}, {"id": "c", "name": "C|D<E>", "zone": "data", "kind": "store"}],
        "flows": [{"src": "a b", "dst": "c", "label": "a|b", "mode": "batch"}]})
    m = to_mermaid(a)
    assert "#34;hi#34; #91;x#93;" in m and "C#124;D#60;E#62;" in m and 'n_a_b -.->|"a#124;b"| n_c' in m
