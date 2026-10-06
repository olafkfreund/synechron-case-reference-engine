import json

import pytest
from pydantic import ValidationError

from app.schema import Outcome, ReferenceCase, Sourced, llm_schema, quote_in, sourced

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


def test_summary_limit():
    with pytest.raises(ValidationError):
        ReferenceCase(title=Sourced[str](), summary="w " * 81)


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


def test_llm_schema_is_strict_and_hides_unsourced():
    schema = llm_schema()
    assert "unsourced" not in json.dumps(schema)
    objs = [d for d in schema["$defs"].values() if d.get("type") == "object"]
    assert objs and all(d.get("additionalProperties") is False for d in objs)
    assert schema.get("additionalProperties") is False
