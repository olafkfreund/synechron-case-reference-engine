"""Lane layout for the solution architecture diagram (#246).

layout() turns an Architecture into a Scene: boxes, lines and text on a 1600x900 canvas, with style
keys instead of colours. The renderers (SVG, PNG, pptx shapes, Mermaid) read the Scene.
Text is measured with the vendored IBM Plex Sans (brand/fonts), so the Scene is the same on every machine.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from math import ceil
from pathlib import Path

from PIL import ImageFont

from app.schema import Architecture

W, H = 1600, 900
FONTS = Path(__file__).resolve().parent.parent / "brand" / "fonts"
WEIGHTS = {400: "Regular", 500: "Medium", 600: "SemiBold"}
# style key -> (size px, weight)
TEXT = {"title": (24, 600), "name": (15, 600), "role": (12, 400), "zone": (12, 600), "label": (11, 500),
        "callout": (13, 500), "legend": (11, 400), "footer": (11, 400), "glyph": (12, 600), "badge": (12, 600)}
GLYPH = {"user": "U", "app": "A", "service": "S", "queue": "Q", "store": "D", "external": "E", "platform": "P"}
ZONES = ["channels", "experience", "integration", "core", "data", "external"]  # left to right; platform is the bottom band

M, HEAD, FOOT_Y = 24, 118, 868
CARD_H, ROW_GAP, SUB_GAP, PAD, LABEL_H = 78, 22, 12, 10, 28
BAND_GAP, PILL_H = 30, 18


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    style: str
    ref: str = ""  # component id for cards


@dataclass
class Line:
    points: list[tuple[float, float]]
    style: str
    arrow: bool = False


@dataclass
class Text:
    x: float
    y: float  # top of the line
    text: str
    style: str
    anchor: str = "start"  # start | middle


@dataclass
class Scene:
    width: int = W
    height: int = H
    zones: list[Box] = field(default_factory=list)  # drawn first
    lines: list[Line] = field(default_factory=list)  # then the connectors, under the cards
    boxes: list[Box] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)


@lru_cache(maxsize=None)
def _font(style: str):
    size, weight = TEXT[style]
    return ImageFont.truetype(str(FONTS / f"IBMPlexSans-{WEIGHTS[weight]}.ttf"), size)


def measure(text: str, style: str) -> float:
    return _font(style).getlength(text)


def fit(text: str, style: str, width: float) -> str:
    """Cut to one line of at most `width`, ending in an ellipsis when something was cut."""
    if measure(text, style) <= width:
        return text
    while text and measure(text + "…", style) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(text: str, style: str, width: float, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        while measure(word, style) > width and len(word) > 1:  # a word wider than the line is broken
            n = len(word)
            while n > 1 and measure(word[:n], style) > width:
                n -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:n])
            word = word[n:]
        if not cur or measure(f"{cur} {word}", style) <= width:
            cur = f"{cur} {word}".strip()
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [fit(" ".join(lines[max_lines - 1:]), style, width)]
    return lines


def _order(arch: Architecture) -> dict[str, list[str]]:
    """Component ids per zone, ordered by barycentre of their neighbours' ranks (2 sweeps)."""
    order = {z: [c.id for c in arch.components if c.zone == z] for z in [*ZONES, "platform"]}
    nbrs: dict[str, set[str]] = {c.id: set() for c in arch.components}
    for f in arch.flows:
        nbrs[f.src].add(f.dst)
        nbrs[f.dst].add(f.src)
    zone_of = {c.id: c.zone for c in arch.components}
    for _ in range(2):
        for z, ids in order.items():
            rank = {i: (n + .5) / len(order[zone_of[i]]) for zone in order.values() for n, i in enumerate(zone)}
            bary = {i: sum(rank[j] for j in nbrs[i] if zone_of[j] != z) / k if (k := sum(zone_of[j] != z for j in nbrs[i])) else rank[i]
                    for i in ids}
            order[z] = sorted(ids, key=lambda i: (bary[i], ids.index(i)))
    return order


def _pill(sc: Scene, cx: float, cy: float, label: str, mode: str, maxw: float):
    if not label:
        return
    if maxw < 48:  # no room between the cards: the mode colour and the legend still say what it is
        return
    text = fit(label, "label", maxw - 12)
    w = measure(text, "label") + 12
    x = min(max(cx - w / 2, 4), W - 4 - w)
    sc.boxes.append(Box(x, cy - PILL_H / 2, w, PILL_H, f"pill-{mode}"))
    sc.texts.append(Text(x + w / 2, cy - 7, text, "label", "middle"))


def layout(arch: Architecture, footer: str = "") -> Scene:
    sc = Scene()
    comp = {c.id: c for c in arch.components}
    order = _order(arch)
    cols = [z for z in ZONES if order[z]]
    plat = order["platform"]
    k = len(cols)

    # header: title left, numbered outcome callouts top-right
    sc.texts.append(Text(M, 30, fit(arch.title, "title", 1060 - M), "title"))
    for n, co in enumerate(arch.callouts[:3]):
        y = 12 + n * 36
        sc.boxes.append(Box(1116, y, 460, 32, "callout"))
        sc.boxes.append(Box(1124, y + 5, 22, 22, "badge"))
        sc.texts.append(Text(1135, y + 9, str(co.outcome + 1), "badge", "middle"))
        sc.texts.append(Text(1156, y + 8, fit(co.text, "callout", 410), "callout"))

    # lanes above the zones for flows that skip a column (greedy interval colouring)
    cidx = {c.id: cols.index(c.zone) for c in arch.components if c.zone in cols}
    spans = sorted(((min(cidx[f.src], cidx[f.dst]), max(cidx[f.src], cidx[f.dst]), n) for n, f in enumerate(arch.flows)
                    if f.src in cidx and f.dst in cidx and abs(cidx[f.src] - cidx[f.dst]) >= 2))
    lane_ends: list[int] = []
    lane_of: dict[int, int] = {}
    for a, b, n in spans:
        for ln, end in enumerate(lane_ends):
            if end < a:
                lane_ends[ln] = b
                break
        else:
            ln = len(lane_ends)
            lane_ends.append(b)
        lane_of[n] = ln
    chan = 10 * len(lane_ends) + 10 if lane_ends else 0
    main_top = HEAD + chan

    # vertical budget: platform band at the bottom, zones above it
    content_bottom = FOOT_Y - 18
    band_h = band_y = rows_p = per_row = 0
    w_card = 230
    main_bottom = content_bottom
    if plat:
        per_row = max(1, int((W - 2 * M + 14) // (w_card + 14)))
        rows_p = ceil(len(plat) / per_row)
        band_h = LABEL_H + rows_p * (CARD_H + ROW_GAP) - ROW_GAP + PAD
        band_y = content_bottom - band_h
        main_bottom = band_y - BAND_GAP
    avail = main_bottom - main_top - LABEL_H - PAD
    cap = max(1, int((avail + ROW_GAP) // (CARD_H + ROW_GAP)))
    subs = {z: ceil(len(order[z]) / cap) for z in cols}
    rows = {z: ceil(len(order[z]) / subs[z]) for z in cols}

    # card width and gutter from the zones in use
    span = W - 2 * M
    G = 0.0
    for w_card in range(230, 129, -10):
        colw = {z: subs[z] * w_card + (subs[z] - 1) * SUB_GAP + 2 * PAD for z in cols}
        G = min(160, (span - sum(colw.values())) / (k - 1)) if k > 1 else 0
        if k < 2 or G >= 96:
            break
    else:
        G = max(G, 0)
    if plat:
        per_row = max(1, int((span + 14) // (w_card + 14)))
        rows_p = ceil(len(plat) / per_row)
        band_h = LABEL_H + rows_p * (CARD_H + ROW_GAP) - ROW_GAP + PAD
        band_y = content_bottom - band_h
    total = sum(colw.values()) + G * max(k - 1, 0)
    x = M + (span - total) / 2
    zbox: dict[str, Box] = {}
    card: dict[str, Box] = {}
    sub_of: dict[str, tuple[int, int]] = {}
    for z in cols:
        zb = Box(x, main_top, colw[z], main_bottom - main_top, "zone")
        zbox[z] = zb
        sc.zones.append(zb)
        sc.texts.append(Text(x + PAD, main_top + 8, z.upper(), "zone"))
        stack = rows[z] * CARD_H + (rows[z] - 1) * ROW_GAP
        y0 = main_top + LABEL_H + (avail - stack) / 2
        for n, i in enumerate(order[z]):
            s, r = divmod(n, rows[z])
            card[i] = Box(x + PAD + s * (w_card + SUB_GAP), y0 + r * (CARD_H + ROW_GAP), w_card, CARD_H, f"card-{comp[i].kind}", i)
            sub_of[i] = (s, r)
        x += colw[z] + G
    if plat:
        zb = Box(M, band_y, span, band_h, "zone")
        zbox["platform"] = zb
        sc.zones.append(zb)
        sc.texts.append(Text(M + PAD, band_y + 8, "PLATFORM", "zone"))
        for n, i in enumerate(plat):
            r, c = divmod(n, per_row)
            nrow = min(per_row, len(plat) - r * per_row)
            x0 = M + (span - (nrow * (w_card + 14) - 14)) / 2
            card[i] = Box(x0 + c * (w_card + 14), band_y + LABEL_H + r * (CARD_H + ROW_GAP), w_card, CARD_H, "card-platform", i)
            sub_of[i] = (0, r)
    for i, b in card.items():
        sc.boxes.append(b)
        c = comp[i]
        sc.boxes.append(Box(b.x + 10, b.y + 10, 24, 24, f"glyph-{c.kind}"))
        sc.texts.append(Text(b.x + 22, b.y + 15, GLYPH[c.kind], "glyph", "middle"))
        tw = b.w - 52
        name = wrap(c.name, "name", tw, 2)
        for n, ln in enumerate(name):
            sc.texts.append(Text(b.x + 42, b.y + 10 + 18 * n, ln, "name"))
        for n, ln in enumerate(wrap(c.role, "role", tw, 3 - len(name))):
            sc.texts.append(Text(b.x + 42, b.y + 10 + 18 * len(name) + 15 * n, ln, "role"))

    # --- flows -------------------------------------------------------------------------------
    def zone_of(i):
        return comp[i].zone

    def kind_of(f):
        a, b = zone_of(f.src), zone_of(f.dst)
        if a == "platform" or b == "platform":
            return "platform-" + ("self" if a == b else "col")
        if a == b:
            return "same"
        return "adj" if abs(cidx[f.src] - cidx[f.dst]) == 1 else "long"

    flows = [f for f in arch.flows if f.src in card and f.dst in card]
    # which side of each card every flow leaves/enters, then spread the ports along the side
    side: dict[tuple[int, str], str] = {}  # (flow no, "s"|"d") -> r|l|t|b
    for n, f in enumerate(flows):
        kd, s, d = kind_of(f), card[f.src], card[f.dst]
        if kd in ("adj", "long"):
            right = cidx[f.dst] > cidx[f.src]
            side[n, "s"], side[n, "d"] = ("r", "l") if right else ("l", "r")
        elif kd == "same":
            (ss, rs), (sd, rd) = sub_of[f.src], sub_of[f.dst]
            if ss == sd and abs(rs - rd) == 1:
                side[n, "s"], side[n, "d"] = ("b", "t") if rd > rs else ("t", "b")
            else:
                side[n, "s"] = side[n, "d"] = "r"
        elif kd == "platform-col":
            for e, i in (("s", f.src), ("d", f.dst)):
                if zone_of(i) == "platform":
                    side[n, e] = "t"
                else:
                    side[n, e] = "r" if cidx[i] < k - 1 else "l"
        else:  # inside the platform band
            if abs(s.y - d.y) < 1:
                side[n, "s"], side[n, "d"] = ("r", "l") if d.x > s.x else ("l", "r")
            else:
                side[n, "s"], side[n, "d"] = ("b", "t") if d.y > s.y else ("t", "b")
    ports: dict[tuple[int, str], tuple[float, float]] = {}
    users: dict[tuple[str, str], list[tuple[float, int, str]]] = {}
    for (n, e), sd in side.items():
        i = flows[n].src if e == "s" else flows[n].dst
        o = card[flows[n].dst if e == "s" else flows[n].src]
        users.setdefault((i, sd), []).append(((o.y + o.h / 2) if sd in "rl" else (o.x + o.w / 2), n, e))
    for (i, sd), lst in users.items():
        b = card[i]
        for p, (_, n, e) in enumerate(sorted(lst)):
            t = (p + 1) / (len(lst) + 1)
            ports[n, e] = {"r": (b.x + b.w, b.y + b.h * t), "l": (b.x, b.y + b.h * t),
                           "t": (b.x + b.w * t, b.y), "b": (b.x + b.w * t, b.y + b.h)}[sd]

    slot: dict[object, int] = {}

    def nxt(key):
        slot[key] = slot.get(key, -1) + 1
        return slot[key]

    def gutter_x(c: int, right: bool) -> tuple[float, float]:
        """Left and right edge of the gutter on one side of column c (the margin at the ends)."""
        zb = zbox[cols[c]]
        return (zb.x + zb.w, zb.x + zb.w + (G if c < k - 1 else M)) if right else (zb.x - (G if c > 0 else M), zb.x)

    for n, f in enumerate(flows):
        kd = kind_of(f)
        (sx, sy), (dx, dy) = ports[n, "s"], ports[n, "d"]
        sd, dd = side[n, "s"], side[n, "d"]
        style = f"flow-{f.mode}"
        pill = None  # (cx, cy, maxw)

        def vx(c, right):  # a vertical run in a gutter, offset 10 per flow
            a, b = gutter_x(c, right)
            j = nxt((c, right)) % 5
            return (a + 12 + 10 * j) if right else (b - 12 - 10 * j)

        if kd == "adj":
            xv = vx(cidx[f.src], sd == "r")
            pts = [(sx, sy), (xv, sy), (xv, dy), (dx, dy)]
            pill = ((xv + dx) / 2, dy, abs(dx - xv) - 6)
        elif kd == "long":
            ly = HEAD + 6 + 10 * lane_of[n]
            xs, xd = vx(cidx[f.src], sd == "r"), vx(cidx[f.dst], dd == "r")
            pts = [(sx, sy), (xs, sy), (xs, ly), (xd, ly), (xd, dy), (dx, dy)]
            pill = ((xd + dx) / 2, dy, abs(dx - xd) - 6)
        elif kd == "same":
            if sd in "tb":
                pts = [(sx, sy), (sx, dy)]
                gap_mid = (sy + dy) / 2
                pill = (sx + 6 + 40, gap_mid, 80)
            else:
                zb = zbox[zone_of(f.src)]
                xr = zb.x + zb.w - 6 - 4 * (nxt(("loop", zone_of(f.src))) % 3)
                pts = [(sx, sy), (xr, sy), (xr, dy), (dx, dy)]
                pill = (zb.x + zb.w + 4 + 40, (sy + dy) / 2, max(G, M) - 8)
        elif kd == "platform-col":
            ly = band_y - BAND_GAP / 2
            src_col = zone_of(f.src) != "platform"
            ci, e = (f.src, sd) if src_col else (f.dst, dd)
            c = cidx[ci]
            xg = vx(c, e == "r")
            if src_col:
                pts = [(sx, sy), (xg, sy), (xg, ly), (dx, ly), (dx, dy)]
            else:
                pts = [(sx, sy), (sx, ly), (xg, ly), (xg, dy), (dx, dy)]
            pill = ((xg + (dx if src_col else sx)) / 2, ly, abs(xg - (dx if src_col else sx)) - 6)
        else:
            if sd in "rl":
                pts = [(sx, sy), (dx, dy)]
                between = any(min(sx, dx) < b.x < max(sx, dx) and b.y < sy < b.y + b.h for i, b in card.items() if i not in (f.src, f.dst))
                pill = ((sx + dx) / 2, sy, -1 if between else abs(dx - sx) - 6)
            else:
                ym = (sy + dy) / 2
                pts = [(sx, sy), (sx, ym), (dx, ym), (dx, dy)]
                pill = ((sx + dx) / 2, ym, abs(dx - sx) - 6)
        sc.lines.append(Line(pts, style, True))
        if pill:
            _pill(sc, pill[0], pill[1], f.label, f.mode, pill[2])

    # footer and legend
    modes = sorted({f.mode for f in flows})
    lx = W - M
    for m in reversed(modes):
        tw = measure(m, "legend")
        lx -= tw
        sc.texts.append(Text(lx, FOOT_Y, m, "legend"))
        lx -= 44
        sc.lines.append(Line([(lx, FOOT_Y + 7), (lx + 36, FOOT_Y + 7)], f"flow-{m}", True))
        lx -= 18
    sc.texts.append(Text(M, FOOT_Y, fit(footer, "footer", lx - M - 16), "footer"))
    return sc
