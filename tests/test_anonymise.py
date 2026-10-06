import uuid

import pytest

from app import anonymise as an, db
from tests.test_auth import ADMIN, REV, client, env  # noqa: F401
from tests.test_review import R, case_data, make  # noqa: F401


def C(name, aliases=(), label="a UK bank", ref=False):
    return dict(name=name, aliases=list(aliases), anonymised_label=label, referenceable=ref)


REG = [C("Acme Bank", ["Acme", "ACME plc"]), C("Acme Bank UK", label="a UK retailer"), C("C++ Ltd", label="a dev shop"),
       C("Globex", label="a manufacturer", ref=True)]


def test_apply_aliases_case_boundaries_possessive_longest_first():
    t = "acme bank uk and Acme Bank. ACME's plan, Acmeville, ACME PLC, C++ Ltd."
    out = an.apply(t, REG)
    assert out == "a UK retailer and a UK bank. a UK bank's plan, Acmeville, a UK bank, a dev shop."


def test_referenceable_untouched_and_blocked_scrub():
    assert an.apply("Globex won", REG) == "Globex won"
    assert an.blocked("Globex and acme", REG) == ["Acme"]
    assert an.blocked("nothing here", REG) == []
    assert an.blocked(an.apply("Acme Bank UK and C++ Ltd", REG), REG) == []
    assert an.scrub("Globex rolled out Acme's Acmeville tool", REG) == "rolled out 's acmeville tool"


def test_regex_metacharacters_and_empty_registry():
    assert an.apply("a.b (x)", [C("a.b (x)", label="L")]) == "L"
    assert an.apply("axb", [C("a.b", label="L")]) == "axb"
    assert an.apply("Acme", []) == "Acme" and an.scrub("Acme", []) == "acme"  # queries are folded
    assert an.apply("Acme", [C("Acme", ["", "  "], label="L")]) == "L"


def test_unlisted_is_exact_not_contains():
    orgs = ["Acme Bank", "acme-bank", "Globex", "Initech", " ", "Initech", "Acme Insurance"]
    # "Acme Insurance" must not hide behind alias "Acme": a possibly confidential sister company
    assert an.unlisted(orgs, REG) == ["Acme Insurance", "Initech"]


def test_review_page_shows_unlisted_organisations_without_llm(make):  # noqa: F811
    cid = make(data=case_data(organisations=["Initech", "Acme Bank"]))
    r = client(R).get(f"/review/{cid}").text  # no complete_json is reachable from here
    assert "organisation not in client registry: Initech" in r


def test_exact_names_with_special_case_folding():
    reg = [C("Straße AG", label="a German firm"), C("Ali", label="a person")]
    assert an.blocked("Straße AG signed", reg) == ["Straße AG"]
    assert an.blocked("STRASSE AG signed", reg) == ["Straße AG"]
    assert an.blocked("ALİ approved", reg) == ["Ali"]  # used to raise KeyError
    an.apply("ALİ approved", reg)


def test_spacing_dash_accent_and_domain_variants_are_caught():
    reg = [C("Acme Bank"), C("Société Générale", label="a French bank")]
    for t in ["Acme\u00a0Bank", "Acme  Bank", "Acme\nBank", "Acme-Bank", "AcmeBank", "jane@acmebank.co.uk"]:
        assert an.blocked(t, reg) == ["Acme Bank"], t
        assert "acme" not in an.scrub(t, reg), t
    nfd = "Socie\u0301te\u0301 Ge\u0301ne\u0301rale"
    assert an.blocked(nfd, reg) == ["Société Générale"]
    assert an.blocked("Societe Generale", reg) == ["Société Générale"]
    assert an.apply(nfd + " won", reg) == "a French bank won"
    assert an.blocked(an.apply("Acme-Bank and AcmeBank", reg), reg) == []


def test_short_caps_alias_is_case_sensitive():
    reg = [C("Acme Corporate Bank", ["ACB"], label="a bank")]
    assert an.apply("ACB and acb", reg) == "a bank and acb"


@pytest.fixture
def reg(env):  # noqa: F811
    db.init()
    tag = uuid.uuid4().hex[:8]
    yield tag
    with db.connect() as c:
        c.execute("delete from clients where name like %s", (f"%{tag}%",))


def find(tag):
    with db.connect() as c:
        return c.execute("select id, name, aliases, anonymised_label, referenceable, logo_allowed, "
                         "md5(row(name, aliases, anonymised_label, referenceable, logo_allowed)::text) "
                         "from clients where name like %s", (f"%{tag}%",)).fetchall()


def test_admin_only(reg):
    assert client([REV]).get("/admin/clients").status_code == 403
    assert client([REV]).post("/admin/clients", data={"name": "x", "anonymised_label": "y"}).status_code == 403


def test_create_update_and_validation(reg):
    c = client([ADMIN])
    name = f"Acme {reg}"
    assert c.post("/admin/clients", data={"name": name, "aliases": " Acm, ,Acm,AcmeCo ", "anonymised_label": "a bank",
                                          "referenceable": "on"}, follow_redirects=False).status_code == 303
    (cid, n, aliases, label, ref, logo, v), = find(reg)
    assert (aliases, ref, logo) == (["Acm", "AcmeCo"], True, False)
    assert name in c.get("/admin/clients").text
    assert c.post("/admin/clients", data={"name": name, "anonymised_label": "x"}).status_code == 400  # duplicate
    assert c.post("/admin/clients", data={"name": f"B {reg}", "anonymised_label": f"the B {reg} bank"}).status_code == 400
    assert c.post("/admin/clients", data={"name": "  ", "anonymised_label": "x"}).status_code == 400
    assert c.post(f"/admin/clients/{cid}", data={"name": name, "anonymised_label": f"{name} group", "v": v}).status_code == 400
    assert c.post(f"/admin/clients/{cid}", data={"name": name, "aliases": "A1", "anonymised_label": "a lender",
                                                 "logo_allowed": "on", "v": v}, follow_redirects=False).status_code == 303
    (_, _, aliases, label, ref, logo, _), = find(reg)
    assert (aliases, label, ref, logo) == (["A1"], "a lender", False, True)
    # stale version -> 409, unknown id -> 404
    assert c.post(f"/admin/clients/{cid}", data={"name": name, "anonymised_label": "z", "v": v}).status_code == 409
    assert c.post("/admin/clients/999999999", data={"name": name, "anonymised_label": "z", "v": v}).status_code == 404


def test_label_may_not_contain_another_clients_protected_name(reg):
    c = client([ADMIN])
    c.post("/admin/clients", data={"name": f"Globex {reg}", "anonymised_label": "a manufacturer"})
    r = c.post("/admin/clients", data={"name": f"Acme {reg}", "anonymised_label": f"a Globex {reg} supplier"})
    assert r.status_code == 400 and "another client" in r.text
