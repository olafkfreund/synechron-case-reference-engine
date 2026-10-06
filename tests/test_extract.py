import uuid

import pytest

from app import db, extract as ex
from app.schema import Outcome, Period, ReferenceCase, Sourced

DOC = ("Acme Bank cut customer onboarding from 12 days to 3 days. "
       "The programme ran for 18 months with a team of 8 engineers, "
       "from January 2023 to June 2024 on AWS and Kubernetes.")
Q = "cut customer onboarding from 12 days to 3 days"


def case(**kw):
    base = dict(
        title=Sourced[str](value="Faster onboarding", source_quote=Q),
        outcomes=[Outcome(metric="onboarding time", value="12 days to 3 days", source_quote=Q)],
        summary="Onboarding fell from 12 days to 3 days.",
    )
    return ReferenceCase(**{**base, **kw})


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
    c = case(outcomes=[Outcome(metric="onboarding time", value="12 days to 1 day", source_quote=Q)])
    assert run(doc, c)[0]["outcomes"][0]["unsourced"] is True


def test_missing_quote_unsourced_and_model_cannot_vouch(doc):
    c = case(title=Sourced[str](value="Faster onboarding", source_quote="", unsourced=False))
    c.outcomes[0].source_quote = "a sentence that is not in the document at all"
    data = run(doc, c)[0]
    assert data["title"]["unsourced"] is True and data["outcomes"][0]["unsourced"] is True


def test_empty_fields_not_flagged(doc):
    data = run(doc, case())[0]
    assert data["region"]["unsourced"] is False and data["period"]["unsourced"] is False


def test_summary_with_invented_number_blanked_and_flagged(doc):
    data, summary, _, _ = run(doc, case(summary="Onboarding fell to 1 day."))
    assert summary == "" and data["summary"] == "" and data["needs_attention"]


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
    c = case(title=Sourced[str](value="Onboarding in 1 day", source_quote="onboarding now takes 1 day only"),
             summary="Onboarding fell to 1 day.")
    data, summary, _, _ = run(doc, c)
    assert data["title"]["unsourced"] is True and summary == ""


def test_period_years_and_zero_team(doc):
    c = case(period=Period(start="2023-01", end="2024-06", source_quote="from January 2023 to June 2024"),
             team_size=Sourced[int](value=0, source_quote="with a team of 8 engineers"))
    data = run(doc, c)[0]
    assert data["period"]["unsourced"] is False and data["team_size"]["unsourced"] is True


def test_literal_fields_must_appear_in_quote(doc):
    q = "from January 2023 to June 2024 on AWS and Kubernetes"
    c = case(tech_stack=[Sourced[str](value="Kubernetes", source_quote=q),
                         Sourced[str](value="Azure", source_quote=q)],
             client_mention=Sourced[str](value="Contoso Bank", source_quote=Q))
    data = run(doc, c)[0]
    assert [t["unsourced"] for t in data["tech_stack"]] == [False, True]
    assert data["client_mention"]["unsourced"] is True


def test_truncation_is_flagged(doc, monkeypatch):
    monkeypatch.setattr(ex, "MAX_CHARS", 20)
    assert any("truncated" in n for n in run(doc, case())[0]["needs_attention"])
