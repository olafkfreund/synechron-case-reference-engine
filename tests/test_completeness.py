from app.completeness import checklist, meets_minimum, score
from app.schema import Diagram, Outcome, Period, Prose, ReferenceCase, Sourced

S = lambda v: Sourced[str](value=v, source_quote="q")
W = lambda n: " ".join(["w"] * n)


def case(**kw):
    return ReferenceCase(title=S("t"), **kw)


def ok(c):
    return {i["key"]: i["ok"] for i in checklist(c)}


def test_old_row_nothing_ok():
    c = case()
    items = checklist(c)
    assert len(items) == 9 and score(items) == (0, 9) and not meets_minimum(items)


def test_words_and_prose_preferred():
    assert ok(case(challenge=S(W(80))))["challenge"]
    assert not ok(case(challenge=S(W(79))))["challenge"]
    # prose wins over points
    c = case(challenge=S(W(100)), prose={"challenge": Prose(text=W(5))})
    assert not ok(c)["challenge"]
    c = case(points={"solution": [S(W(40)), S(W(40))]})
    assert ok(c)["solution"]


def test_unsourced_not_counted():
    u = Sourced[str](value=W(100), unsourced=True)
    assert not ok(case(challenge=u, points={"challenge": [u]}))["challenge"]
    caps = [S("a"), S("b"), Sourced[str](value="c", unsourced=True)]
    assert not ok(case(capabilities=caps))["capabilities"]
    assert ok(case(capabilities=caps + [S("d")]))["capabilities"]
    assert not ok(case(outcomes=[Outcome(metric="m", value="v", unsourced=True)]))["results"]


def test_each_item():
    c = case(implementation=S("x"), configuration=S("y"), tech_stack=[S("a"), S("b"), S("c")],
             outcomes=[Outcome(metric="m", value="v")], period=Period(start="2024"),
             diagrams=[Diagram(id=1, caption="c", status="ready")])
    r = ok(c)
    assert all(v for k, v in r.items() if k not in ("challenge", "solution", "capabilities"))
    assert not ok(case(diagrams=[Diagram(id=1, caption="c", status="pending")]))["diagram"]
    assert ok(case(duration_months=Sourced[int](value=6, source_quote="q")))["period"]


def test_engagement_results():
    n = Sourced[int](value=6, source_quote="q")
    assert not ok(case(basis="engagement", duration_months=n))["results"]
    assert not ok(case(basis="engagement", solution=S("s")))["results"]
    assert ok(case(basis="engagement", solution=S("s"), duration_months=n))["results"]
    # delivered with the same fields is not ok
    assert not ok(case(solution=S("s"), duration_months=n))["results"]


def test_minimum_boundary():
    base = dict(challenge=S(W(80)), solution=S(W(80)))
    assert not meets_minimum(checklist(case(**base, implementation=S("x"))))
    assert meets_minimum(checklist(case(**base, implementation=S("x"), configuration=S("y"))))
    assert not meets_minimum(checklist(case(solution=S(W(80)), implementation=S("x"), configuration=S("y"))))


def test_detail_wording_states_the_minimum():
    d = {i["key"]: i["detail"] for i in checklist(case(challenge=S(W(69))))}
    assert d["challenge"] == "69 words (minimum 80)" and d["capabilities"] == "0 (minimum 3)"
