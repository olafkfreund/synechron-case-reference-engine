from app import writeup as wu
from app.schema import ReferenceCase, Sourced

Q1 = "migrated the legacy ledger to Snowflake in 7 weeks"
Q2 = "the team of 5 engineers ran three phases"


def case(**points):
    c = ReferenceCase(title=Sourced[str]())
    c.points = {f: [Sourced[str](value=v, source_quote=q) for v, q in ps] for f, ps in points.items()}
    return c


def stub(monkeypatch, **fields):
    calls = []

    def fake(alias, system, user, model, **k):
        calls.append((user, k))
        return wu.Writeup(**fields)
    monkeypatch.setattr(wu, "complete_json", fake)
    return calls


def test_check_prose_flags_numbers_names_and_prices():
    assert wu.check_prose("The ledger moved to Snowflake in 7 weeks.", [Q1], [Q1]) == []
    assert wu.check_prose("It took 9 weeks.", [Q1], [Q1]) == ["number 9 is in no quote"]
    assert wu.check_prose("They chose Databricks for it.", [Q1], [Q1]) == ["names 'Databricks', which no quote supports"]
    assert wu.check_prose("Fees of £1,200 per day applied.", ["x"], ["x"])[-1] == "contains a price or payment term"


def test_check_prose_sentence_start_is_not_a_name_unless_acronym_or_digit():
    assert wu.check_prose("Delivery began early.\n\nPlanning followed.", [Q1], [Q1]) == []
    assert wu.check_prose("SLA terms applied.", [Q1], [Q1]) == ["names 'SLA', which no quote supports"]
    assert wu.check_prose("Phase 2 began.", [Q1], [Q1]) != []  # a digit in a start term is checked


def test_write_sets_prose_with_flags_and_notes(monkeypatch):
    c = case(solution=[("Ledger moved", Q1)], implementation=[("Three phases", Q2)])
    calls = stub(monkeypatch, solution="The ledger moved to Snowflake in 7 weeks.",
                 implementation="The team of 9 engineers ran three phases.", challenge="Invented text.")
    wu.write(c, "confidential")
    assert calls[0][1] == {"data_class": "confidential"} and "Ledger moved" in calls[0][0]
    assert c.prose["solution"].flags == [] and c.prose["implementation"].flags == ["number 9 is in no quote"]
    assert "challenge" not in c.prose  # no points for it
    assert any("implementation write-up" in n for n in c.needs_attention)


def test_write_skips_model_with_no_points_and_survives_failure(monkeypatch):
    calls = stub(monkeypatch)
    c = case()
    wu.write(c, "internal")
    assert calls == [] and c.prose == {}
    c = case(solution=[("Ledger moved", Q1)])
    monkeypatch.setattr(wu, "complete_json", lambda *a, **k: (_ for _ in ()).throw(ValueError("echo Zorbulon")))
    wu.write(c, "internal")
    assert c.prose == {} and c.needs_attention == ["write-up failed; the points are shown (ValueError)"]


def test_write_summary_only_when_sourced(monkeypatch):
    c = case(solution=[("Ledger moved", Q1)])
    c.solution = Sourced[str](value="Ledger moved", source_quote=Q1)
    c.summary = "old"
    stub(monkeypatch, solution="Done.", summary="It took 9 weeks.")
    wu.write(c, "internal")
    assert c.summary == "old"
    stub(monkeypatch, solution="Done.", summary="It took 7 weeks.")
    wu.write(c, "internal")
    assert c.summary == "It took 7 weeks."


def test_check_prose_sentence_start_checks_the_rest_of_the_run():
    assert wu.check_prose("The Snowflake team led it.", [Q1.replace("Snowflake", "x")], [Q1.replace("Snowflake", "x")]) == [
        "names 'Snowflake', which no quote supports"]
    assert wu.check_prose("The Snowflake team led it.", [Q1], [Q1]) == []


def test_check_prose_numbers_are_per_section_names_per_case():
    assert wu.check_prose("It took 5 weeks.", [Q1], [Q1, Q2]) == ["number 5 is in no quote"]  # 5 is in another section
    assert wu.check_prose("They chose Kubernetes.", [Q1], [Q1, "runs on Kubernetes clusters"]) == []


def test_check_prose_ignores_month_and_weekday_names():
    assert wu.check_prose("In March 2024 the go-live moved to Fri 7 weeks on.", [Q1], [Q1]) == ["number 2024 is in no quote"]
    assert wu.check_prose("It began in Mar, not Zanzibar.", [Q1], [Q1]) == ["names 'Zanzibar', which no quote supports"]
