import json

import pytest

from app.schema import Extraction, Outcome, ReferenceCase, Sourced, llm_schema, numbers, plain, quote_in, sourced

DOC = "The bank cut onboarding from 12 days to\n3 days.  Team “Alpha” – 8 people."


def make():
    return ReferenceCase(
        title=Sourced[str](value="Onboarding", source_quote="cut onboarding"),
        capabilities=[Sourced[str](value="KYC", source_quote="QUOTE-CAP")],
        outcomes=[Outcome(metric="onboarding", value="12 to 3 days", source_quote="QUOTE-OUT")],
        summary="Faster onboarding.",
    )


def test_round_trip():
    c = make()
    assert ReferenceCase.model_validate_json(c.model_dump_json()) == c


def test_json_schema_self_contained():
    s = ReferenceCase.model_json_schema()
    assert "properties" in s
    # nested models live under $defs; every $ref must point inside this document
    refs = [v for v in _walk(s) if isinstance(v, str) and v.startswith("#")]
    assert refs and all(r.startswith("#/$defs/") for r in refs)


def _walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "$ref":
                yield v
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def test_quote_in_whitespace_quotes_dashes():
    assert quote_in(DOC, "12 days   to 3\ndays")
    assert quote_in(DOC, 'Team "Alpha" - 8 people')
    assert quote_in(DOC, "the bank cut onboarding")  # case-insensitive
    assert not quote_in(DOC, "")


def test_quote_in_rejects_invented_metric():
    assert not quote_in(DOC, "cut onboarding from 12 days to 1 day")


def test_search_text_excludes_quotes():
    t = make().search_text()
    assert "Onboarding" in t and "KYC" in t and "12 to 3 days" in t
    assert "QUOTE" not in t and "source_quote" not in t


def test_quote_in_word_and_pdf_artifacts():
    doc = "Programme trans-\nformation\u00ad finished\u200b early\u2026 on time."
    assert quote_in(doc, "Programme transformation finished early... on time")


def test_sourced_ties_value_to_quote():
    q = "cut onboarding from 12 days to"
    assert sourced("12 days", q, DOC)
    assert not sourced("12 to 1 days", q, DOC)  # real quote, invented number
    assert not sourced("Retail banking", "The", DOC)  # trivial quote
    assert sourced("bank", "The bank", DOC)  # short quote that contains the value


def test_summary_numbers_must_come_from_quotes():
    c = make()
    c.outcomes[0].source_quote = "cut onboarding from 12 days to 3 days"
    c.summary = "Onboarding fell from 12 to 3 days."
    assert c.summary_sourced()
    c.summary = "Onboarding fell from 12 to 2 days."
    assert not c.summary_sourced()


# (token, reading): plan #44, never looser than main: only thousands commas are dropped
@pytest.mark.parametrize("tok,want", [
    ("14", {"14"}),
    ("1,200", {"1200"}),
    ("1,200,000", {"1200000"}),
    ("1,20,000", {"120000"}),
    ("1,234.5", {"1234.5"}),
    ("1,5", {"1,5"}),
    ("12,34", {"12,34"}),
    ("1.234,5", {"1.234,5"}),
    ("1.200", {"1.200"}),
    ("3.50", {"3.50"}),
    (0, {"0"}),
    (None, set()),
])
def test_numbers_readings(tok, want):
    assert numbers(tok) == want


def test_sourced_number_formats():
    doc = "1,5 Mio. EUR saved per year. 1,200 users were onboarded."
    assert not sourced(15, "1,5 Mio. EUR saved per year", doc)  # the #44 bug: "1,5" read as 15
    assert sourced("1,5 Mio", "1,5 Mio. EUR saved per year", doc)
    assert sourced("1200", "1,200 users were onboarded", doc)
    assert not sourced("1.2", "1,200 users were onboarded", doc)


def test_llm_schema_is_strict_and_hides_unsourced():
    schema = llm_schema()
    assert "unsourced" not in json.dumps(schema) and "document_id" not in json.dumps(schema)
    objs = [d for d in schema["$defs"].values() if d.get("type") == "object"]
    assert objs and all(d.get("additionalProperties") is False for d in objs)
    assert schema.get("additionalProperties") is False


def test_assemble_flat_items():
    from app.schema import Extraction, Item, assemble
    it = lambda f, v, q="some quote of four words": Item(field=f, value=v, quote=q)
    x = Extraction(items=[
        it("title", "First"), it("title", "Second"), it("team_size", "a team of 1,200"), it("team_size", "9"),
        it("duration_months", "none"), it("capability", "A"), it("capability", "B"),
        it("technology", "AWS"), it("technology", "Kubernetes"), it("organisation", "Acme"),
        it("organisation", "Acme"), it("outcome", "onboarding: 12 days to 3"), it("outcome", "faster"),
        it("period_start", "2023-01"), it("period_end", "2024-06"), it("nope", "x"), it("title", " ")],
        summary="w " * 90)
    c, notes = assemble(x)
    assert c.title.value == "First" and c.team_size.value == 1200 and c.duration_months.value is None
    assert [s.value for s in c.capabilities] == ["A", "B"] and [s.value for s in c.tech_stack] == ["AWS", "Kubernetes"]
    assert c.organisations == ["Acme"] and (c.period.start, c.period.end) == ("2023-01", "2024-06")
    assert (c.outcomes[0].metric, c.outcomes[0].value) == ("onboarding", "12 days to 3")
    assert (c.outcomes[1].metric, c.outcomes[1].value) == ("", "faster")
    assert len(c.summary.split()) == 80 and notes == ["3 malformed or unknown item(s) skipped", "summary trimmed to 80 words"]


@pytest.mark.parametrize("metric,value,vague", [
    ("", "x", True), ("Metric", "x", True), (" result ", "x", True), ("Faster", "faster", True),
    ("claim handling time", "cut by 38 percent", False), ("order value", "up 5 percent", False)])
def test_vague_metric(metric, value, vague):
    from app.schema import vague_metric
    assert vague_metric(Outcome(metric=metric, value=value)) is vague


def test_item_is_lenient():
    from app.schema import Extraction
    x = Extraction.model_validate({"items": [{"field": "title", "value": 5, "extra": 1}], "summary": ""})
    assert x.items[0].value == "5"


def test_long_summary_is_trimmed_not_rejected():
    assert len(ReferenceCase(title=Sourced[str](), summary="w " * 90).summary.split()) == 80


def test_assemble_takes_the_first_number_only():
    from app.schema import Extraction, Item, assemble
    case, _ = assemble(Extraction(items=[Item(field="duration_months", value="18 months to 2 years", quote="q"),
                                         Item(field="team_size", value="a team of 1,200", quote="q")]))
    assert case.duration_months.value == 18 and case.team_size.value == 1200


def test_assemble_skips_runaway_numbers():
    from app.schema import Extraction, Item, assemble
    for runaway in ("1" * 4301, "1" * 13):
        case, notes = assemble(Extraction(items=[Item(field="team_size", value=runaway, quote="q")]))
        assert case.team_size.value is None and "1 malformed" in notes[0]
    case, _ = assemble(Extraction(items=[Item(field="team_size", value="1,000,000", quote="q")]))
    assert case.team_size.value == 1000000


def test_null_values_do_not_fail_the_reply():
    from app.schema import Extraction
    x = Extraction.model_validate_json('{"items": [{"field": "industry", "value": null, "quote": null}], "summary": ""}')
    assert x.items[0].value == ""


def test_extraction_schema_requires_its_keys():
    from app.schema import Extraction, llm_schema
    s = llm_schema(Extraction)
    assert set(s["required"]) == {"items", "summary"}
    assert set(s["$defs"]["Item"]["required"]) == {"field", "value", "quote"}


def test_basis_hidden_from_llm_and_search():
    c = make()
    assert c.basis == "delivered"
    assert "basis" not in json.dumps(llm_schema())
    c.basis, c.basis_reason = "engagement", "ZZREASON"
    assert "engagement" not in c.search_text() and "ZZREASON" not in c.search_text()
    assert "ZZREASON" not in "".join(c.quotes())


def test_old_case_data_validates_without_new_sections():
    # rows written before #198 have none of the new keys, nor by_reviewer
    d = {"title": {"value": "T", "source_quote": "q", "unsourced": False, "document_id": None}}
    c = ReferenceCase.model_validate(d)
    assert not c.implementation.value and not c.lessons_learned.by_reviewer
    assert c.title.by_reviewer is False


def test_by_reviewer_hidden_from_llm_schema():
    assert "by_reviewer" not in json.dumps(llm_schema())
    assert "by_reviewer" not in json.dumps(llm_schema(Extraction))


def test_reviewer_text_does_not_vouch_for_summary_numbers():
    c = make()
    c.implementation = Sourced[str](value="Rolled out in 9 weeks", by_reviewer=True)
    c.summary = "Delivered in 9 weeks."
    assert not c.summary_sourced()


def test_diagrams_hidden_from_llm_schema():
    assert "diagrams" not in ReferenceCase.model_json_schema()["properties"]
    assert "diagrams" not in json.dumps(llm_schema())
    old = {"title": {"value": "T"}}  # data saved before #200 has no diagrams key
    assert ReferenceCase.model_validate(old).diagrams == []


def test_points_keep_every_distinct_item_and_stay_hidden():
    from app.schema import MAX_POINTS, Extraction, Item, assemble
    its = [Item(field="challenge", value=v, quote=v) for v in ("Slow  intake", "slow intake", "Manual triage")]
    its += [Item(field="solution", value=f"s{i}", quote=f"s{i}") for i in range(MAX_POINTS + 3)]
    c, _ = assemble(Extraction(items=its))
    assert c.challenge.value == "Slow  intake"  # headline: first wins
    assert [p.value for p in c.points["challenge"]] == ["Slow  intake", "Manual triage"]
    assert len(c.points["solution"]) == MAX_POINTS
    assert "Manual triage" in c.quotes() and "Manual triage" in c.search_text()
    assert "points" not in json.dumps(llm_schema())
    assert ReferenceCase.model_validate({"title": {"value": "T"}}).points == {}
    assert ReferenceCase.model_validate(c.model_dump()).points == c.points


def test_prose_round_trips_is_searchable_and_hidden():
    from app.schema import Prose
    c = make()
    assert c.prose == {} and ReferenceCase.model_validate({"title": {"value": "T"}}).prose == {}
    c.prose = {"challenge": Prose(text="Zanzibar intake was slow.", by_reviewer=True, flags=["Zanzibar"])}
    assert ReferenceCase.model_validate(c.model_dump()).prose == c.prose
    assert "Zanzibar intake" in c.search_text()
    assert "prose" not in json.dumps(llm_schema())


def test_references_are_not_a_main_extraction_field_and_stay_searchable():
    from app.schema import FIELDS, Extraction, Item, References, assemble
    assert "reference" not in FIELDS and "tech_references" not in json.dumps(llm_schema(Extraction))
    c, notes = assemble(Extraction(items=[Item(field="reference", value="ISO 20022", quote="ISO 20022 messaging")]))
    assert c.tech_references == [] and notes  # an unknown field is skipped and counted
    c.tech_references = [Sourced[str](value="ISO 20022", source_quote="ISO 20022 messaging")]
    assert "ISO 20022 messaging" in c.quotes() and "ISO 20022" in c.search_text()
    assert ReferenceCase.model_validate({"title": {"value": "T"}}).tech_references == []
    assert ReferenceCase.model_validate(c.model_dump()).tech_references == c.tech_references
    r = References.model_validate_json('{"items": [{"value": null, "quote": "q", "extra": 1}]}')
    assert r.items[0].value == "" and "items" in llm_schema(References)["required"]


@pytest.mark.parametrize("raw,want", [
    ("**Zor**pale runs it", "Zorpale runs it"),
    ("see [the docs](https://x.io/a_b) now", "see the docs now"),
    ("![diagram of flow](https://x.io/i.png) shows it", "diagram of flow shows it"),
    ("see [the docs][1] now", "see the docs now"),
    ("cited [1][2] twice", "cited [1][2] twice"),
    ("go to <https://x.io/a> today", "go to https://x.io/a today"),
    ("it is __very__ fast and *really* good", "it is very fast and really good"),
    ("set `max_conn` to 5", "set max_conn to 5"),
    ("snake_case_name stays and 2*3*4 too", "snake_case_name stays and 2*3*4 too"),
    ("## Heading text", "Heading text"),
    ("> a quoted line\n> another line", "a quoted line another line"),
    ('""Quoted"" inside text', '"Quoted" inside text'),
    ('"A whole wrapped quote here"', "A whole wrapped quote here"),
    ("\u201cCurly wrapped quote here\u201d", "Curly wrapped quote here"),
    ('"One" and "two" stay', '"One" and "two" stay'),
    ("  many \n\t spaces  ", "many spaces"),
])
def test_plain(raw, want):
    assert plain(raw) == want


def test_plain_does_not_change_what_quote_in_matches():
    page = "We say **Zor**pale runs it, see [docs](https://x.io)."
    assert quote_in(page, "**Zor**pale runs it, see [docs](https://x.io)") and not quote_in(page, plain("**Zor**pale runs it, see [docs](https://x.io)"))
