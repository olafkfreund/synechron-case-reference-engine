import uuid

import pytest

from app import db, extract as ex
from app.schema import Extraction, Item, ReferenceCase

DOC = ("Acme Bank cut customer onboarding from 12 days to 3 days. "
       "The programme ran for 18 months with a team of 8 engineers, "
       "from January 2023 to June 2024 on AWS and Kubernetes.")
Q = "cut customer onboarding from 12 days to 3 days"


def case(*extra, summary="Onboarding fell from 12 days to 3 days.", title=("title", "Faster onboarding", Q),
         outcome=("outcome", "onboarding time: 12 days to 3 days", Q)):
    """What the model replies: flat items; (field, value, quote) tuples."""
    items = [Item(field=f, value=v, quote=q) for f, v, q in (title, outcome, *extra) if f]
    return Extraction(items=items, summary=summary)


@pytest.fixture
def doc(monkeypatch):
    db.init()
    with db.connect() as c:
        sid = c.execute("insert into sources(kind,name) values ('s3',%s) returning id",
                        (uuid.uuid4().hex,)).fetchone()[0]
        did = c.execute("insert into documents(source_id,external_id,checksum,text) "
                        "values (%s,'a','x',%s) returning id", (sid, DOC)).fetchone()[0]
    reply = {}
    monkeypatch.setattr(ex, "complete_json", lambda *a, **k: reply["v"])
    yield did, reply
    with db.connect() as c:
        c.execute("delete from sources where id=%s", (sid,))


def run(doc, c):
    did, reply = doc
    reply["v"] = c
    ex.extract(did)
    with db.connect() as conn:
        return conn.execute("select data, summary, search_text, status from cases where document_id=%s",
                            (did,)).fetchone()


def test_good_fields_sourced(doc):
    data, summary, _, status = run(doc, case())
    assert status == "extracted" and summary
    assert data["title"]["unsourced"] is False and data["outcomes"][0]["unsourced"] is False
    assert data["needs_attention"] == []
    ReferenceCase.model_validate(data)  # later steps rebuild the case from data


def test_invented_metric_unsourced(doc):
    c = case(outcome=("outcome", "onboarding time: 12 days to 1 day", Q))
    assert run(doc, c)[0]["outcomes"][0]["unsourced"] is True


def test_missing_quote_unsourced_and_model_cannot_vouch(doc):
    c = case(title=("title", "Faster onboarding", ""),
             outcome=("outcome", "onboarding time: 12 days to 3 days", "a sentence that is not in the document at all"))
    c.items[0] = Item.model_validate({"field": "title", "value": "Faster onboarding", "quote": "", "unsourced": False})
    data = run(doc, c)[0]
    assert data["title"]["unsourced"] is True and data["outcomes"][0]["unsourced"] is True


def test_empty_fields_not_flagged(doc):
    data = run(doc, case())[0]
    assert data["region"]["unsourced"] is False and data["period"]["unsourced"] is False


def test_summary_with_invented_number_blanked_and_flagged(doc):
    data, summary, _, _ = run(doc, case(summary="Onboarding fell to 1 day."))
    assert summary == "" and data["summary"] == "" and data["needs_attention"]


@pytest.mark.parametrize("s,hit", [
    ("£1,200/day", True), ("USD 450 per hour", True), ("payment within 30 days", True),
    ("€2.5m fixed price", True), ("1.2m GBP", True), ("rate of 900 p.d.", True),
    ("30 servers", False), ("phase 2", False), ("18 months", False), ("a team of 8", False),
    ("Payments modernisation", False), ("SWIFT payment messaging", False), ("10m transactions/month", False),
    # prices in other spellings (review of #52)
    ("1.2 million pounds", True), ("1,200 euros", True), ("₹50 lakh", True), ("SGD 200,000", True),
    ("HKD 1m", True), ("JPY 5m", True), ("daily rate of 900", True), ("900 per man-day", True),
    ("per diem", True), ("total contract value 1.2m", True), ("budget of 2 million", True),
    ("GBP 1,200", True), ("£ 1,200", True), ("S$200k", True), ("professional fees of 40k", True),
    ("invoiced monthly in arrears", True), ("payable within 45 days", True),
    ("Rs. 50,000", True), ("Rs 50,000", True), ("2.5 mn EUR", True), ("950k per day", True), ("80k/hour", True),
    # banking scope that must survive
    ("2 million payments/day", False), ("5m messages per day", False), ("interchange fees", False),
    ("fee and commission engine", False), ("e-invoicing platform", False), ("invoice financing", False),
    ("accounts payable automation", False), ("purchase order matching", False), ("budgeting module", False),
])
def test_commercial_filter_table(s, hit):
    assert bool(ex.COMMERCIAL.search(s)) is hit


def test_engagement_drops_outcomes_and_commercial_items(doc):
    did, reply = doc
    reply["v"] = case(("outcome", "metric: 12 days to 3 days", Q), ("capability", "Onboarding", Q), ("capability", "Rate", "billed at £1,200/day for 18 months"),
                      ("technology", "AWS", "on AWS and Kubernetes"), ("duration_months", "18 months", "ran for 18 months"),
                      ("team_size", "8", "a team of 8 engineers"))
    ex.extract(did, "engagement", "executed contract")
    with db.connect() as c:
        data, basis = c.execute("select data, basis from cases where document_id=%s", (did,)).fetchone()
    assert basis == "engagement" and data["basis"] == "engagement" and data["basis_reason"] == "executed contract"
    assert data["outcomes"] == []
    assert [x["value"] for x in data["capabilities"]] == ["Onboarding"]
    assert data["duration_months"]["value"] == 18 and data["team_size"]["value"] == 8
    assert data["needs_attention"] == ["1 item(s) with prices, rates or payment terms removed"]


def test_vague_outcome_metric_is_noted(doc):
    data, *_ = run(doc, case(outcome=("outcome", "metric: 12 days to 3 days", Q)))
    assert data["needs_attention"] == ["1 outcome(s) with no clear metric"]
    data, *_ = run(doc, case(("outcome", "faster onboarding", Q), outcome=("outcome", "metric: 12 days to 3 days", Q)))
    assert data["needs_attention"] == ["2 outcome(s) with no clear metric"]
    data, *_ = run(doc, case())
    assert data["needs_attention"] == []


def test_delivered_keeps_outcomes_and_default_basis(doc):
    data, *_ = run(doc, case())
    assert data["basis"] == "delivered" and len(data["outcomes"]) == 1


def test_reextraction_resets_approval_and_search_text_has_no_quotes(doc):
    did, _ = doc
    run(doc, case())
    with db.connect() as c:
        c.execute("update cases set status='approved', approved_by='r', approved_at=now(), "
                  "review_due=now() where document_id=%s", (did,))
    _, _, search_text, status = run(doc, case())
    assert status == "extracted" and "Faster onboarding" in search_text and "contiguous" not in search_text
    assert Q not in search_text
    with db.connect() as c:
        assert c.execute("select approved_by, approved_at, review_due from cases where document_id=%s",
                         (did,)).fetchone() == (None, None, None)


def test_unsourced_quote_cannot_launder_summary_number(doc):
    c = case(title=("title", "Onboarding in 1 day", "onboarding now takes 1 day only"),
             summary="Onboarding fell to 1 day.")
    data, summary, _, _ = run(doc, c)
    assert data["title"]["unsourced"] is True and summary == ""


def test_period_years_and_zero_team(doc):
    pq = "from January 2023 to June 2024"
    c = case(("period_start", "2023-01", pq), ("period_end", "2024-06", pq),
             ("team_size", "0 engineers", "with a team of 8 engineers"))
    data = run(doc, c)[0]
    assert data["period"]["unsourced"] is False and data["team_size"]["unsourced"] is True


def test_literal_fields_must_appear_in_quote(doc):
    q = "from January 2023 to June 2024 on AWS and Kubernetes"
    c = case(("technology", "Kubernetes", q), ("technology", "Azure", q),
             ("client_mention", "Contoso Bank", Q))
    data = run(doc, c)[0]
    assert [t["unsourced"] for t in data["tech_stack"]] == [False, True]
    assert data["client_mention"]["unsourced"] is True


def test_truncation_is_flagged(doc, monkeypatch):
    monkeypatch.setattr(ex, "MAX_CHARS", 20)
    assert any("truncated" in n for n in run(doc, case())[0]["needs_attention"])


def test_malformed_and_overlong_are_noted_and_case_survives(doc):
    c = case(("bogus", "x", Q), ("technology", "", Q), summary="word " * 100)
    data, summary, _, _ = run(doc, c)
    notes = data["needs_attention"]
    assert any("2 malformed" in n for n in notes) and any("trimmed" in n for n in notes)
    assert data["title"]["value"] == "Faster onboarding" and data["tech_stack"] == []


def test_invented_period_with_month_names_is_unsourced(doc):
    pq = "from January 2023 to June 2024"
    data = run(doc, case(("period_start", "March 2031", pq), ("period_end", "December 2039", pq)))[0]
    assert data["period"]["unsourced"] is True  # "Marc"[:4] used to match anything
    data = run(doc, case(("period_start", "March", pq)))[0]
    assert data["period"]["unsourced"] is True  # no year at all never passes
    data = run(doc, case(("period_start", "January 2023", pq), ("period_end", "June 2024", pq)))[0]
    assert data["period"]["unsourced"] is False


def test_price_in_numeric_item_quote_clears_the_item():
    from app.schema import Period, ReferenceCase, Sourced
    case = ReferenceCase(
        title=Sourced[str](value="Core migration", source_quote="the core migration programme"),
        duration_months=Sourced[int](value=1200, source_quote="will charge £1,200 per day for 9 months"),
        team_size=Sourced[int](value=8, source_quote="a team of 8 engineers"),
        period=Period(start="2026", end="2027", source_quote="at a total contract value of 1.2m from 2026"))
    assert ex.strip_commercial(case) == 2
    assert case.duration_months.value is None and case.period.start is None
    assert case.team_size.value == 8 and case.title.value == "Core migration"


def test_outcome_without_value_unsourced():
    from app.schema import Outcome, ReferenceCase, Sourced
    case = ReferenceCase(title=Sourced[str](value="Core migration", source_quote="the core migration programme"),
                         outcomes=[Outcome(metric="Revenue", value="", source_quote=Q),
                                   Outcome(metric="", value="  ", source_quote=Q),
                                   Outcome(metric="onboarding", value="12 days to 3 days", source_quote=Q)])
    ex.check(case, DOC)
    assert [o.unsourced for o in case.outcomes] == [True, True, False]


def test_merged_case_checks_each_item_against_its_own_document():
    from app.schema import Sourced
    qa, qb = "built the onboarding portal for staff", "migrated the payroll platform to cloud"
    texts = {1: f"Contract A. {qa}.", 2: f"Contract B. {qb}."}

    def run(doc_id, quote, value):
        c = ReferenceCase(title=Sourced[str](value=value, source_quote=quote, document_id=doc_id))
        ex.check(c, texts)
        return c.title

    assert not run(1, qa, qa).unsourced
    assert run(1, qb, qb).unsourced  # quote is only in B
    assert run(99, qa, qa).unsourced  # not a member
    t = run(None, qb, qb)  # a reviewer's Add: stamped with the first member that sources it
    assert not t.unsourced and t.document_id == 2
    assert run(None, "nowhere in either", "nowhere in either").unsourced


def cases_for(did):
    with db.connect() as c:
        return c.execute("select status from cases where document_id=%s", (did,)).fetchall()


def test_stale_checksum_writes_no_case(doc, monkeypatch):
    did, _ = doc
    calls = []
    monkeypatch.setattr(ex, "complete_json", lambda *a, **k: calls.append(1) or case())
    ex.extract(did, checksum="old")
    assert cases_for(did) == [] and calls == []


def test_version_replaced_during_model_call_writes_no_case(doc, monkeypatch):
    did, _ = doc

    def replace(*a, **k):
        with db.connect(autocommit=True) as c:
            c.execute("update documents set checksum='y' where id=%s", (did,))
        return case()

    monkeypatch.setattr(ex, "complete_json", replace)
    ex.extract(did, checksum="x")
    assert cases_for(did) == []


def test_no_checksum_still_extracts(doc):
    did, reply = doc
    reply["v"] = case()
    ex.extract(did)
    assert cases_for(did) == [("extracted",)]
