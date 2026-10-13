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
