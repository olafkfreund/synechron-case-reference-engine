"""Is an approved case complete enough to present (#222)? Pure: reads the case, calls no model."""
CHECKS = {"challenge_words": 80, "solution_words": 80, "capabilities": 3, "technologies": 3}  # tune here only
MINIMUM_OTHERS = 2


def section_text(case, f: str) -> str:
    """The written prose when there is some, else the headline plus the sourced points."""
    p = case.prose.get(f)
    if p and p.text.strip():
        return p.text
    head = getattr(case, f)
    parts = [head.value] if head.value and not head.unsourced else []
    parts += [x.value for x in case.points.get(f, []) if x.value and not x.unsourced]
    return " ".join(parts)


def _words(case, f: str, key: str):
    n = len(section_text(case, f).split())
    return n >= CHECKS[key], f"{n} of {CHECKS[key]} words"


def _count(items, key: str):
    n = sum(1 for i in items if i.value and not i.unsourced)
    return n >= CHECKS[key], f"{n} of {CHECKS[key]}"


def checklist(case) -> list[dict]:
    rows = [("challenge", "Challenge", *_words(case, "challenge", "challenge_words")),
            ("solution", "Solution", *_words(case, "solution", "solution_words"))]
    for f in ("implementation", "configuration"):
        n = len(section_text(case, f).split())
        rows.append((f, f.capitalize(), n > 0, f"{n} words" if n else "empty"))
    rows.append(("capabilities", "Capabilities", *_count(case.capabilities, "capabilities")))
    rows.append(("technologies", "Technologies", *_count(case.tech_stack, "technologies")))
    months = bool(case.duration_months.value) and not case.duration_months.unsourced
    if case.basis == "engagement":
        has_sol = bool(section_text(case, "solution").strip())
        rows.append(("results", "Results", has_sol and months,
                     f"solution {'yes' if has_sol else 'no'}, duration {'yes' if months else 'no'}"))
    else:
        n = sum(1 for o in case.outcomes if not o.unsourced)
        rows.append(("results", "Results", n >= 1, f"{n} sourced outcomes"))
    start = bool(case.period.start) and not case.period.unsourced
    rows.append(("period", "Period", start or months, "found" if start or months else "missing"))
    n = sum(1 for d in case.diagrams if d.status == "ready")
    arch = bool(case.architecture and case.architecture.components)  # an architecture meets it too (#246)
    rows.append(("diagram", "Diagram", n >= 1 or arch, f"{n} ready" + (", architecture" if arch else "")))
    return [dict(key=k, label=l, ok=ok, detail=d) for k, l, ok, d in rows]


def meets_minimum(items: list[dict]) -> bool:
    ok = {i["key"]: i["ok"] for i in items}
    return ok["challenge"] and ok["solution"] and sum(v for k, v in ok.items() if k not in ("challenge", "solution")) >= MINIMUM_OTHERS


def score(items: list[dict]) -> tuple[int, int]:
    return sum(i["ok"] for i in items), len(items)
