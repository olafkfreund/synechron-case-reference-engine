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
])
def test_commercial_filter_table(s, hit):
    assert bool(ex.COMMERCIAL.search(s)) is hit


def test_engagement_drops_outcomes_and_commercial_items(doc):
    did, reply = doc
    reply["v"] = case(("capability", "Onboarding", Q), ("capability", "Rate", "billed at £1,200/day for 18 months"),
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
