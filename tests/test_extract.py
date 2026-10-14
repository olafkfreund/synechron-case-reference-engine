import json
import uuid

import pytest
from psycopg.types.json import Jsonb

from app import db, extract as ex
from app.schema import Extraction, Item, Ref, ReferenceCase, References, Sourced, llm_schema

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
    monkeypatch.setattr(ex, "complete_json", lambda a, system, user, model, **k: References() if model is References else reply["v"])
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


def test_reextraction_keeps_diagrams(doc):
    did, _ = doc
    assert run(doc, case())[0]["diagrams"] == []  # a first extraction still has the key
    entry = {"id": 7, "caption": "Flow", "status": "ready", "reason": "", "width": 10, "height": 5}
    with db.connect() as c:
        c.execute("update cases set data = data || jsonb_build_object('diagrams', %s::jsonb) where document_id=%s",
                  (json.dumps([entry]), did))
    data, _, _, status = run(doc, case())
    assert data["diagrams"] == [entry] and status == "extracted"


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


def test_extract_waits_for_an_uncommitted_new_version(doc, monkeypatch):
    """The write-time check takes a share lock: a version ingest has not committed yet is waited for (#132)."""
    import threading
    from tests.test_ingest import _wait_for_lock_wait
    did, reply = doc
    reply["v"] = case()
    holder = db.connect()  # stands in for ingest: new checksum written, not committed
    try:
        holder.execute("update documents set checksum='y' where id=%s", (did,))
        t = threading.Thread(target=ex.extract, args=(did,), kwargs={"checksum": "x"})
        t.start()
        _wait_for_lock_wait(holder)
        holder.commit()
    finally:
        holder.close()
    t.join(10)
    assert not t.is_alive() and cases_for(did) == []


def test_no_checksum_still_extracts(doc):
    did, reply = doc
    reply["v"] = case()
    ex.extract(did)
    assert cases_for(did) == [("extracted",)]


TICK = ("update sources set config = config || '{\"executed_contracts\": true}' "
        "where id = (select source_id from documents where id=%s)")
UNTICK = ("update sources set config = jsonb_set(config, '{executed_contracts}', 'false') "  # as the admin save writes it
          "where id = (select source_id from documents where id=%s)")


def test_flagged_job_on_unticked_source_writes_no_case_and_calls_no_model(doc, monkeypatch):
    did, _ = doc

    def boom(*a, **k):
        raise AssertionError("model called")

    monkeypatch.setattr(ex, "complete_json", boom)
    ex.extract(did, "engagement", "source marked executed")
    assert cases_for(did) == []


def test_untick_during_flagged_extract_writes_no_case(doc, monkeypatch):
    did, _ = doc
    with db.connect() as c:
        c.execute(TICK, (did,))

    def untick(*a, **k):
        with db.connect(autocommit=True) as c:
            c.execute(UNTICK, (did,))
        return case()

    monkeypatch.setattr(ex, "complete_json", untick)
    ex.extract(did, "engagement", "source marked executed")
    assert cases_for(did) == []


def test_executed_contract_reason_ignores_the_flag(doc):
    did, reply = doc
    reply["v"] = case()
    ex.extract(did, "engagement", "executed contract")
    assert cases_for(did) == [("extracted",)]


def test_flagged_extract_waits_for_an_uncommitted_untick(doc):
    """The write-time flag check takes a share lock on the source: an untick not yet committed is waited for (#159)."""
    import threading
    from tests.test_ingest import _wait_for_lock_wait
    did, reply = doc
    reply["v"] = case()
    with db.connect() as c:
        c.execute(TICK, (did,))
    errors = []

    def run():
        try:
            ex.extract(did, "engagement", "source marked executed")
        except Exception as e:  # a crash after the wait must fail the test, not pass it
            errors.append(e)

    holder = db.connect()  # stands in for the admin save: flag removed, not committed
    try:
        holder.execute(UNTICK, (did,))
        t = threading.Thread(target=run)
        t.start()
        _wait_for_lock_wait(holder)
        holder.commit()
    finally:
        holder.close()
    t.join(10)
    assert not t.is_alive() and errors == [] and cases_for(did) == []


def test_extract_clears_retired_by(doc):
    did, _ = doc
    run(doc, case())
    with db.connect() as c:
        c.execute("update cases set status='rejected', retired_by='untick' where document_id=%s", (did,))
    run(doc, case())
    with db.connect() as c:
        assert c.execute("select status, retired_by from cases where document_id=%s", (did,)).fetchone() == ("extracted", None)


def test_new_sections_extracted_and_checked(doc):
    data, *_ = run(doc, case(("implementation", "Phased rollout", "The programme ran for 18 months"),
                             ("configuration", "Kubernetes on AWS", "on AWS and Kubernetes"),
                             ("lessons_learned", "Start earlier", "invented passage not in the document")))
    assert data["implementation"]["value"] == "Phased rollout" and data["implementation"]["unsourced"] is False
    assert data["configuration"]["unsourced"] is False
    assert data["lessons_learned"]["unsourced"] is True


def test_engagement_never_gets_lessons_learned(doc):
    did, reply = doc
    reply["v"] = case(("lessons_learned", "Start earlier", "The programme ran for 18 months"))
    ex.extract(did, "engagement", "executed contract")
    with db.connect() as c:
        data = c.execute("select data from cases where document_id=%s", (did,)).fetchone()[0]
    assert data["lessons_learned"]["value"] is None


@pytest.mark.parametrize("f", ["implementation", "configuration", "lessons_learned"])
def test_strip_commercial_clears_new_sections(f):
    c = ReferenceCase(title=Sourced[str]())
    setattr(c, f, Sourced[str](value="Fixed-price delivery", source_quote="a fixed-price delivery of scope"))
    assert ex.strip_commercial(c) == 1 and getattr(c, f).value is None


def test_check_leaves_reviewer_text_sourced():
    c = ReferenceCase(title=Sourced[str](), implementation=Sourced[str](value="Our own words", by_reviewer=True, unsourced=True))
    ex.check(c, {1: DOC})
    assert c.implementation.unsourced is False and c.implementation.document_id is None


def reviewer_text(did, f="implementation"):
    with db.connect() as c:
        data = c.execute("select data from cases where document_id=%s", (did,)).fetchone()[0]
        data[f] = {"value": "Our own words", "source_quote": "", "unsourced": False, "document_id": None, "by_reviewer": True}
        c.execute("update cases set data=%s where document_id=%s", (Jsonb(data), did))


def test_reextract_keeps_reviewer_written_section_with_note(doc):
    run(doc, case())
    reviewer_text(doc[0])
    data, *_ = run(doc, case())
    assert data["implementation"]["value"] == "Our own words" and data["implementation"]["by_reviewer"] is True
    assert data["needs_attention"] == ["Implementation approach: your text from before the document changed was kept; check it still holds"]


def test_reextract_quote_replaces_reviewer_text(doc):
    run(doc, case())
    reviewer_text(doc[0])
    data, *_ = run(doc, case(("implementation", "Phased rollout", "The programme ran for 18 months")))
    assert data["implementation"]["value"] == "Phased rollout" and data["implementation"]["by_reviewer"] is False
    assert data["needs_attention"] == ["Implementation approach: the document now states this; your earlier text was replaced"]


def test_reextract_engagement_drops_reviewer_lessons(doc):
    did, reply = doc
    run(doc, case())
    reviewer_text(did, "lessons_learned")
    reply["v"] = case()
    ex.extract(did, "engagement", "executed contract")
    with db.connect() as c:
        data = c.execute("select data from cases where document_id=%s", (did,)).fetchone()[0]
    assert data["lessons_learned"]["value"] is None and data["needs_attention"] == []


def test_check_ignores_by_reviewer_on_other_fields():
    c = ReferenceCase(title=Sourced[str](value="Made up", by_reviewer=True))
    ex.check(c, "some document text")
    assert c.title.unsourced is True


def test_chunks_cut_at_blank_lines_and_overlap():
    t = "\n\n".join(f"para {i} " + "x" * 90 for i in range(30))
    parts = ex.chunks(t, 500, overlap=120)
    assert len(parts) > 1 and all(len(p) <= 500 for p in parts)
    assert parts[0].endswith("\n\n") and parts[1][:100] in t[len(parts[0]) - 120:]
    assert ex.chunks("short", 500) == ["short"]
    assert len(ex.chunks("y" * 1200, 500, overlap=100)) == 3  # no blank line: hard cut


def test_chunk_chars(monkeypatch):
    monkeypatch.setenv("EXTRACT_MODEL", "ollama_chat/m")
    monkeypatch.setattr(ex, "profile", lambda a: {"num_ctx": 24576})
    assert ex.chunk_chars() == (24576 - ex.MAX_TOKENS - 2000) * 3
    monkeypatch.setattr(ex, "profile", lambda a: {})
    assert ex.chunk_chars() is None


def test_build_reads_long_text_in_parts_and_keeps_points(monkeypatch):
    text = "\n\n".join(f"Part {i}: the bank migrated system {i} in {i + 2} weeks." for i in range(40))
    monkeypatch.setattr(ex, "chunk_chars", lambda: 400)
    seen = []

    def fake(alias, system, user, model, **k):
        if model is References:  # the references pass is not what these tests count
            return References()
        seen.append(user)
        i = len(seen)
        q = f"the bank migrated system {i} in {i + 2} weeks"
        return Extraction(items=[Item(field="title", value="T", quote=q),
                                 Item(field="solution", value=f"migrated {i}", quote=q)],
                          summary=f"Done in {i + 2} weeks." if i == 2 else "")
    monkeypatch.setattr(ex, "complete_json", fake)
    c = ex.build(text, "internal")
    n = len(seen)
    assert n > 2 and f"read in {n} parts" in c.needs_attention
    assert len(c.points["solution"]) == n and c.solution.value == "migrated 1"
    assert c.summary == "Done in 4 weeks."  # first summary its items vouch for
    assert all(not p.unsourced for p in c.points["solution"])


def test_check_marks_points_like_their_field():
    c = ReferenceCase(title=Sourced[str]())
    c.points = {"solution": [Sourced[str](value="made up", source_quote="not in the document at all")],
                "implementation": [Sourced[str](value="Reviewer text", by_reviewer=True)],
                "challenge": [Sourced[str](value="Slow", source_quote="cut customer onboarding from 12 days")]}
    ex.check(c, DOC)
    s, i, ch = c.points["solution"][0], c.points["implementation"][0], c.points["challenge"][0]
    assert s.unsourced and not i.unsourced and not ch.unsourced
    c.summary = "Fell by 99 percent."
    assert c.summary_sourced() is False  # an unsourced point's quote vouches for nothing


def test_strip_commercial_filters_points():
    c = ReferenceCase(title=Sourced[str]())
    c.points = {"solution": [Sourced[str](value="Fixed scope", source_quote="fees of £1,200 per day apply"),
                             Sourced[str](value="Migrate the ledger", source_quote="migrate the ledger to the cloud")]}
    assert ex.strip_commercial(c) == 1
    assert [p.value for p in c.points["solution"]] == ["Migrate the ledger"]


def test_chunk_chars_refuses_a_context_too_small_to_step(monkeypatch):
    monkeypatch.setenv("EXTRACT_MODEL", "ollama_chat/m")
    monkeypatch.setattr(ex, "profile", lambda a: {"num_ctx": ex.MAX_TOKENS + 2000})
    with pytest.raises(ValueError, match="num_ctx too small"):
        ex.chunk_chars()


def test_build_summary_not_vouched_by_an_unsourced_quote(monkeypatch):
    text = "\n\n".join(f"Part {i}: the bank migrated system {i} in {i + 2} weeks." for i in range(40))
    monkeypatch.setattr(ex, "chunk_chars", lambda: 400)
    seen = []

    def fake(alias, system, user, model, **k):
        if model is References:  # the references pass is not what these tests count
            return References()
        seen.append(user)
        if len(seen) == 1:  # a fabricated quote carries 99; the first summary leans on it
            return Extraction(items=[Item(field="title", value="T", quote="the bank migrated system 0 in 2 weeks"),
                                     Item(field="solution", value="x", quote="saved 99 percent of cost overnight")],
                              summary="Saved 99 percent.")
        q = "the bank migrated system 1 in 3 weeks"
        return Extraction(items=[Item(field="solution", value="y", quote=q)],
                          summary="Done in 3 weeks." if len(seen) == 2 else "")
    monkeypatch.setattr(ex, "complete_json", fake)
    c = ex.build(text, "internal")
    assert c.points["solution"][0].unsourced and c.summary == "Done in 3 weeks."


def test_three_chunks_via_num_ctx_source_a_quote_only_in_the_last(monkeypatch):
    monkeypatch.setenv("EXTRACT_MODEL", "ollama_chat/m")
    monkeypatch.setenv("EXTRACT_MODEL_OPTIONS", json.dumps({"num_ctx": 12000}))
    size = ex.chunk_chars()
    paras = [f"Filler paragraph {i}. " + "lorem ipsum " * 60 for i in range(14)]  # ~10k characters: 3 pieces of ~5.4k
    text = "\n\n".join(paras) + "\n\nThe bank retired the legacy ledger in 7 weeks."
    seen = []

    def fake(alias, system, user, model, **k):
        if model is References:  # the references pass is not what these tests count
            return References()
        seen.append(user)
        if "retired the legacy ledger" in user:
            return Extraction(items=[Item(field="solution", value="Ledger retired", quote="retired the legacy ledger in 7 weeks")])
        return Extraction()
    monkeypatch.setattr(ex, "complete_json", fake)
    c = ex.build(text, "internal")
    assert len(seen) == 3 and "read in 3 parts" in c.needs_attention
    assert not c.points["solution"][0].unsourced


def test_engagement_keeps_points_and_drops_lessons(monkeypatch):
    q = "the bank migrated system 1 in 3 weeks"
    text = f"Scope: {q}. Fees of £1,200 per day apply to the migration work."
    monkeypatch.setattr(ex, "complete_json", lambda a, system, user, model, **k: References() if model is References else Extraction(items=[
        Item(field="solution", value="Migrate system 1", quote=q),
        Item(field="solution", value="Migrate system 2", quote=q),
        Item(field="solution", value="Priced work", quote="Fees of £1,200 per day apply"),
        Item(field="lessons_learned", value="Start earlier", quote=q)]))
    c = ex.build(text, "internal", "engagement", "executed contract")
    assert [p.value for p in c.points["solution"]] == ["Migrate system 1", "Migrate system 2"]
    assert c.lessons_learned.value is None


def test_capability_is_an_offering_without_results_or_prices(monkeypatch):
    from app import writeup
    q = "we propose to migrate system 1 in 3 weeks"
    text = f"Offer: {q}. Fees of £1,200 per day apply to the migration work."
    seen = []

    def fake(alias, system, user, model, **k):
        seen.append((system, user))
        if model is References:
            return References()
        if model is writeup.Writeup:
            return writeup.Writeup()
        if model is writeup.ArchProposal:
            return writeup.ArchProposal(components=[writeup.ArchItem(name="System", zone="core", kind="service", cite="solution 1")],
                                        callouts=[writeup.ArchNote(text="3 weeks", outcome=1)])
        return Extraction(items=[
            Item(field="solution", value="Migrate system 1", quote=q),
            Item(field="solution", value="Priced work", quote="Fees of £1,200 per day apply"),
            Item(field="outcome", value="migration time: 3 weeks", quote=q),
            Item(field="lessons_learned", value="Start earlier", quote=q)])
    monkeypatch.setattr(ex, "complete_json", fake)
    monkeypatch.setattr(writeup, "complete_json", fake)
    c = ex.build(text, "internal", "capability")
    assert c.basis == "capability" and c.outcomes == [] and c.lessons_learned.value is None
    assert [p.value for p in c.points["solution"]] == ["Migrate system 1"]  # the commercial strip ran
    assert c.architecture.callouts == []
    assert ex.CAPABILITY_SYSTEM in [s for s, _ in seen]
    assert any("basis: capability" in u for _, u in seen) and "Never use past-tense or result language" in writeup.WRITEUP_SYSTEM


def test_chunks_ignore_a_blank_line_near_the_start():
    t = "head\n\n" + "z" * 1200  # the only blank line is at the start
    parts = ex.chunks(t, 500, overlap=100)
    assert len(parts[0]) == 500 and all(len(p) >= 400 for p in parts[:-1])
    t = "head\n\n" + ("y" * 90 + "\n") * 14  # no usable blank line, but newlines in the second half
    assert ex.chunks(t, 500, overlap=100)[0].endswith("\n")


def test_blanked_note_when_only_a_later_chunk_has_a_failing_summary(monkeypatch):
    text = "\n\n".join(f"Part {i}: the bank migrated system {i} in {i + 2} weeks." for i in range(40))
    monkeypatch.setattr(ex, "chunk_chars", lambda: 400)
    n = []

    def fake(alias, system, user, model, **k):
        n.append(1)
        q = "the bank migrated system 1 in 3 weeks"
        return Extraction(items=[Item(field="title", value="T", quote=q)],
                          summary="Saved 99 percent." if len(n) == 2 else "")
    monkeypatch.setattr(ex, "complete_json", fake)
    c = ex.build(text, "internal")
    assert c.summary == "" and any("summary used numbers absent" in x for x in c.needs_attention)


def test_bedrock_is_never_chunked(monkeypatch):
    monkeypatch.setenv("EXTRACT_MODEL", "bedrock/m")
    monkeypatch.setenv("EXTRACT_MODEL_OPTIONS", json.dumps({"num_ctx": 12000}))
    assert ex.chunk_chars() is None and ex.split("x" * 100_000) == ["x" * 100_000]
    monkeypatch.setenv("EXTRACT_MODEL", "ollama_chat/m")
    assert ex.chunk_chars() == (12000 - ex.MAX_TOKENS - 2000) * 3


def reviewer_prose(did, f="solution", text="Our own write-up."):
    with db.connect() as c:
        data = c.execute("select data from cases where document_id=%s", (did,)).fetchone()[0]
        data["prose"] = {f: {"text": text, "by_reviewer": True, "flags": []}}
        c.execute("update cases set data=%s where document_id=%s", (Jsonb(data), did))


def test_build_writes_prose_from_points_and_merges_notes(monkeypatch):
    from app import writeup
    q = "cut customer onboarding from 12 days to 3 days"
    monkeypatch.setattr(ex, "complete_json", lambda a, system, user, model, **k: References() if model is References else case(("solution", "Faster onboarding", q)))
    monkeypatch.setattr(writeup, "complete_json", lambda *a, **k: writeup.Writeup(solution="Onboarding took 9 days."))
    c = ex.build(DOC, "internal")
    assert c.prose["solution"].flags == ["number 9 is in no quote"]
    assert any("solution write-up has 1 unsupported" in n for n in c.needs_attention)


def test_reextract_keeps_reviewer_prose_over_a_new_model_one(doc, monkeypatch):
    from app import writeup
    q = "cut customer onboarding from 12 days to 3 days"
    monkeypatch.setattr(writeup, "complete_json", lambda *a, **k: writeup.Writeup(solution="Onboarding was faster."))
    run(doc, case(("solution", "Faster onboarding", q)))
    reviewer_prose(doc[0])
    data, *_ = run(doc, case(("solution", "Faster onboarding", q)))
    assert data["prose"]["solution"] == {"text": "Our own write-up.", "by_reviewer": True, "flags": []}
    assert "Solution: your write-up from before the document changed was kept; check it still holds" in data["needs_attention"]


def test_reextract_keeps_a_reviewer_architecture_over_a_new_proposal(doc, monkeypatch):
    from app import writeup
    q = "cut customer onboarding from 12 days to 3 days"
    monkeypatch.setattr(writeup, "complete_json", lambda *a, **k: writeup.Writeup() if a[3] is writeup.Writeup else writeup.ArchProposal(
        components=[writeup.ArchItem(name="Onboarding", zone="core", kind="service", cite="solution 1")]))
    run(doc, case(("solution", "Faster onboarding", q)))
    with db.connect() as c:
        data = c.execute("select data from cases where document_id=%s", (doc[0],)).fetchone()[0]
        data["architecture"] = {"title": "Solution architecture", "components": [], "flows": [], "callouts": [], "by_reviewer": True, "flags": []}
        c.execute("update cases set data=%s where document_id=%s", (Jsonb(data), doc[0]))
    data, *_ = run(doc, case(("solution", "Faster onboarding", q)))
    assert data["architecture"]["title"] == "Solution architecture" and data["architecture"]["by_reviewer"] is True
    assert any(n.startswith("Architecture: your diagram") for n in data["needs_attention"])


def reviewer_arch(did, cite="solution 1"):
    arch = {"title": "Solution architecture", "components": [{"id": "c1", "name": "Onboarding", "role": "", "zone": "core", "kind": "service", "cite": cite}],
            "flows": [], "callouts": [], "by_reviewer": True, "flags": []}
    with db.connect() as c:
        data = c.execute("select data from cases where document_id=%s", (did,)).fetchone()[0]
        data["architecture"] = arch
        c.execute("update cases set data=%s where document_id=%s", (Jsonb(data), did))


def test_reextract_recomputes_a_kept_architectures_flags_and_asks_for_a_fresh_confirmation(doc, monkeypatch):
    from app import writeup
    q = "cut customer onboarding from 12 days to 3 days"
    calls = []
    monkeypatch.setattr(writeup, "complete_json", lambda a, s, u, model, **k: calls.append(model) or model())
    run(doc, case(("solution", "Faster onboarding", q)))
    reviewer_arch(doc[0])
    calls.clear()
    data, *_ = run(doc, case(("solution", "Faster onboarding", q)))  # still supported: kept and confirmed
    assert data["architecture"]["by_reviewer"] is True and data["architecture"]["flags"] == []
    assert writeup.ArchProposal not in calls  # a kept diagram costs no proposal call
    data, *_ = run(doc, case())  # the point is gone: flags appear and the confirmation is withdrawn
    assert data["architecture"]["flags"] and data["architecture"]["by_reviewer"] is False


def test_reextract_drops_model_prose_but_keeps_reviewer_prose_when_nothing_new(doc):
    run(doc, case())
    reviewer_prose(doc[0], "challenge")
    data, *_ = run(doc, case())
    assert list(data["prose"]) == ["challenge"] and data["prose"]["challenge"]["by_reviewer"] is True


def test_reextract_recomputes_the_flags_of_kept_reviewer_prose(doc):
    run(doc, case())
    reviewer_prose(doc[0], "solution", "It took 99 days.")  # stored with no flags
    data, *_ = run(doc, case(("solution", "Faster onboarding", Q)))
    assert data["prose"]["solution"]["by_reviewer"] is True and data["prose"]["solution"]["flags"] == ["number 99 is in no quote"]


def test_build_stamps_the_extraction_version(monkeypatch):
    monkeypatch.setattr(ex, "complete_json", lambda a, system, user, model, **k: References() if model is References else case())
    assert ex.build(DOC, "internal").extraction_version == ex.EXTRACTION_VERSION
    assert "extraction_version" not in json.dumps(llm_schema())


def test_extraction_version_is_pinned():
    assert ex.EXTRACTION_VERSION == 4  # a change to extraction output must bump this


NOTE = "Re-extracted with richer extraction: check and approve again"


def test_reextracting_an_old_row_adds_the_note_and_keeps_reviewer_prose(doc):
    did, _ = doc
    run(doc, case())
    with db.connect() as c:
        data = c.execute("select data from cases where document_id=%s", (did,)).fetchone()[0]
        data.pop("extraction_version")
        data["prose"] = {"challenge": {"text": "Mine", "by_reviewer": True, "flags": []}}
        c.execute("update cases set data=%s where document_id=%s", (Jsonb(data), did))
    data, *_ = run(doc, case())
    assert NOTE in data["needs_attention"] and data["prose"]["challenge"]["text"] == "Mine"
    assert data["extraction_version"] == ex.EXTRACTION_VERSION


def test_reextracting_a_current_row_has_no_note(doc):
    run(doc, case())
    assert NOTE not in run(doc, case())[0]["needs_attention"]


def test_reextract_keeps_dismissed_images_and_diagrams(doc):
    run(doc, case())
    with db.connect() as c:
        data = c.execute("select data from cases where document_id=%s", (doc[0],)).fetchone()[0]
        data["dismissed_images"], data["diagrams"] = [7], [{"id": 3, "caption": "c", "status": "ready", "image_id": 7}]
        c.execute("update cases set data=%s where document_id=%s", (Jsonb(data), doc[0]))
    data, *_ = run(doc, case())
    assert data["dismissed_images"] == [7] and data["diagrams"][0]["id"] == 3


def test_references_are_checked_literally_and_stripped_of_prices():
    c = ReferenceCase(title=Sourced[str]())
    c.tech_references = [Sourced[str](value="ISO 20022", source_quote="payments follow ISO 20022 messaging"),
                         Sourced[str](value="SWIFT gpi", source_quote="payments follow ISO 20022 messaging"),  # not in its quote
                         Sourced[str](value="£1,200 per day", source_quote="fees of £1,200 per day apply")]
    ex.check(c, "payments follow ISO 20022 messaging and fees of £1,200 per day apply")
    assert [r.unsourced for r in c.tech_references] == [False, True, False]
    assert ex.strip_commercial(c) == 1 and [r.value for r in c.tech_references] == ["ISO 20022", "SWIFT gpi"]


def test_main_prompts_have_no_reference_field_and_the_pass_has_its_own(monkeypatch):
    for prompt in (ex.SYSTEM, ex.CONTRACT_SYSTEM, ex.CAPABILITY_SYSTEM):
        assert "reference" not in prompt.lower()  # a `reference` field in the main prompt cut capabilities and technologies in half
    seen = []

    def fake(alias, system, user, model, **k):
        seen.append((system, user, model, k["data_class"]))
        return case() if model is Extraction else References(items=[Ref(value="OAuth 2.0", quote="supports OAuth 2.0 sign in")])
    monkeypatch.setattr(ex, "complete_json", fake)
    c = ex.build("The portal supports OAuth 2.0 sign in for staff. " + DOC, "internal")
    assert [r.value for r in c.tech_references] == ["OAuth 2.0"] and not c.tech_references[0].unsourced
    refs_call = next(s for s in seen if s[2] is References)
    assert refs_call[3] == "internal" and refs_call[0] == ex.REFERENCES_SYSTEM and refs_call[1].startswith("<document>\n") and refs_call[1].endswith("\n</document>")


def test_references_pass_dedupes_caps_strips_prices_and_is_literal_checked(monkeypatch):
    items = [Ref(value=f"Std {i}", quote=f"the platform follows Std {i} closely") for i in range(30)]
    items += [Ref(value="std 3", quote="the platform follows Std 3 closely"),   # a duplicate
              Ref(value="Rate", quote="fees of £1,200 per day apply"),           # a price
              Ref(value="", quote="empty value")]
    classes = []
    monkeypatch.setattr(ex, "complete_json", lambda *a, **k: (classes.append(k["data_class"]), References(items=items))[1])
    refs, notes = ex.extract_references(["x"], "confidential")
    assert classes == ["confidential"]  # the points quote the document, so its class is passed through
    assert [r.value for r in refs] == [f"Std {i}" for i in range(20)] and notes == [] and len(refs) == ex.MAX_REFS
    c = ReferenceCase(title=Sourced[str]())
    c.tech_references = [Sourced[str](value="Std 1", source_quote="the platform follows Std 1 closely"),
                         Sourced[str](value="Std 9", source_quote="the platform follows Std 1 closely")]  # not in its quote
    ex.check(c, "the platform follows Std 1 closely")
    assert [r.unsourced for r in c.tech_references] == [False, True]


def test_a_failing_references_pass_never_fails_the_build(monkeypatch):
    def fake(alias, system, user, model, **k):
        if model is References:
            raise RuntimeError("echo Zorbulon")
        return case()
    monkeypatch.setattr(ex, "complete_json", fake)
    c = ex.build(DOC, "internal")
    assert c.tech_references == [] and c.title.value == "Faster onboarding"
    assert c.needs_attention == ["technical references could not be read from every part (RuntimeError)"]
    assert "Zorbulon" not in " ".join(c.needs_attention)


def test_references_pass_reads_every_part_and_one_failed_part_loses_only_itself(monkeypatch):
    calls = []

    def fake(alias, system, user, model, **k):
        calls.append(user)
        n = len(calls)
        if n == 3:
            raise RuntimeError("part 3 broke")
        return References(items=[Ref(value=f"Std {n}", quote=f"follows Std {n} closely")])
    monkeypatch.setattr(ex, "complete_json", fake)
    refs, notes = ex.extract_references(["p1", "p2", "p3", "p4"], "internal")
    assert len(calls) == 4 and [r.value for r in refs] == ["Std 1", "Std 2", "Std 4"]  # the other parts are still read
    assert notes == ["technical references could not be read from every part (RuntimeError)"]  # once, not per failure
    assert all(f"<document>\np{i}\n</document>" == u for i, u in enumerate(calls, 1))
