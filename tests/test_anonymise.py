import uuid

import pytest

from app import anonymise as an, db
from tests.test_auth import ADMIN, REV, client, env  # noqa: F401
from tests.test_review import DOCS, R, case_data, make  # noqa: F401


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


def test_referenceable_name_containing_protected_name():
    reg = [C("Zorp", label="a retailer"),
           C("Zorp Logistics", ["ZL Freight", "Zorp Freight"], label="a logistics firm", ref=True),
           C("Northwind", label="a wholesaler", ref=True)]
    for s, want in [("Zorp Logistics won", "a logistics firm won"),
                    ("Zorp Freight shipped", "a logistics firm shipped"),
                    ("ZL Freight shipped", "ZL Freight shipped"),
                    ("Zorp and Northwind", "a retailer and Northwind")]:
        assert an.apply(s, reg) == want
        assert an.blocked(an.apply(s, reg), reg) == []


def test_label_backslash_is_text_and_many_clients_stay_fast():
    import time
    reg = [C("Zorp", label=r"a \d retailer"), C("Zorp Logistics", label=r"a \g<0> firm", ref=True)]
    assert an.apply("Zorp and Zorp Logistics", reg) == r"a \d retailer and a \g<0> firm"
    many = [C(f"Client{i}", [f"Alias{i}"], label="a firm") for i in range(200)]
    many += [C(f"Partner{i}", [f"Other{i}"], label="a partner", ref=True) for i in range(200)]
    t0 = time.perf_counter()
    an.apply("some text here", many)
    assert time.perf_counter() - t0 < 0.5  # was ~1.2 s with one search per name pair


def test_shown():
    assert an.shown("Globex", "a manufacturer", True, True) == "Globex"
    assert an.shown("Globex", "a manufacturer", False, True) == "a manufacturer"
    assert an.shown(None, None, None, False) == "a client"
    assert an.shown("Globex", "a manufacturer", True, False) == "a client"


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


def test_add_unlisted_client_from_review_page(make, reg):  # noqa: F811
    name = f"Initech {reg}"
    cid = make(data=case_data(organisations=[name]))
    note = f"organisation not in client registry: {name}"
    admin_page = client([ADMIN, DOCS]).get(f"/review/{cid}").text
    assert note in admin_page and f'name="next" value="/review/{cid}"' in admin_page
    reviewer_page = client(R).get(f"/review/{cid}").text
    assert note in reviewer_page and 'action="/admin/clients"' not in reviewer_page
    r = client([ADMIN, DOCS]).post("/admin/clients", follow_redirects=False,
                                   data={"name": name, "anonymised_label": "a software firm", "next": f"/review/{cid}"})
    assert r.status_code == 303 and r.headers["location"] == f"/review/{cid}"
    assert note not in client([ADMIN, DOCS]).get(f"/review/{cid}").text


@pytest.mark.parametrize("i, nxt", enumerate(["https://evil.example", "//evil.example", "/review/1/../x", "/review/", "",
                                              "/review/1x", "/review/1\n"]))
def test_create_redirects_only_to_a_review_page(reg, i, nxt):
    r = client([ADMIN]).post("/admin/clients", follow_redirects=False,
                             data={"name": f"Globex {reg} {i}", "anonymised_label": "a firm", "next": nxt})
    assert r.status_code == 303 and r.headers["location"] == "/admin/clients"


def test_add_from_review_needs_admin_and_a_clean_label(reg):
    name = f"Initech {reg}"
    data = {"name": name, "anonymised_label": "a software firm", "next": "/review/1"}
    assert client([REV]).post("/admin/clients", data=data).status_code == 403
    data["anonymised_label"] = f"the Initech {reg} people"
    assert client([ADMIN]).post("/admin/clients", data=data).status_code == 400
    assert find(reg) == []


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


def test_short_caps_alias_matches_any_case():
    reg = [C("Acme Corporate Bank", ["ACB"], label="a bank")]
    out = an.apply("ACB and acb", reg)
    assert out == "a bank and a bank"
    assert an.blocked(out, reg) == []


def test_hyphenated_name_matches_spaced_joined_and_dashed():
    reg = [C("Zorp-Tek", [], label="a maker")]
    for form in ("Zorp-Tek", "Zorp Tek", "ZorpTek", "Zorp\u2013Tek"):
        assert an.apply(f"{form} won", reg) == "a maker won", form
        assert an.blocked(form, reg) == ["Zorp-Tek"], form


def test_dash_only_name_does_not_match_everything():
    assert an.blocked("plain text", [C("-", [], label="x")]) == []


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
    assert c.post("/admin/clients", data={"name": name, "aliases": " Acm\n\nAcm\nAcmeCo ", "anonymised_label": "a bank",
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


def _add(c, name, label, ref=False, aliases=""):
    data = {"name": name, "anonymised_label": label, "aliases": aliases, **({"referenceable": "on"} if ref else {})}
    return c.post("/admin/clients", data=data, follow_redirects=False)


def _row(tag, name):
    return next(r for r in find(tag) if r[1] == name)


def test_new_protected_name_in_another_label_refused(reg):
    c = client([ADMIN])
    zl = f"Zorp Logistics {reg}"
    assert _add(c, zl, f"a Quill{reg} partner", ref=True).status_code == 303
    r = _add(c, f"Quill{reg}", "a publisher")
    assert r.status_code == 400 and zl in r.json()["detail"]
    assert [x[1] for x in find(reg)] == [zl]
    zp = f"Zorp Packaging {reg}"  # a protected client's label counts too
    assert _add(c, zp, f"a Quill{reg} supplier").status_code == 303
    r = _add(c, f"Quill{reg}", "a publisher")
    assert r.status_code == 400 and zp in r.json()["detail"]


def test_unticking_referenceable_refused_when_name_in_a_label(reg):
    c = client([ADMIN])
    q = f"Quill{reg}"
    assert _add(c, q, "a publisher", ref=True).status_code == 303
    assert _add(c, f"Zorp Logistics {reg}", f"a {q} partner", ref=True).status_code == 303
    cid, *_, v = _row(reg, q)
    r = c.post(f"/admin/clients/{cid}", data={"name": q, "anonymised_label": "a publisher", "v": v})
    assert r.status_code == 400 and f"Zorp Logistics {reg}" in r.json()["detail"]
    assert _row(reg, q)[4] is True


def test_new_alias_in_another_label_refused(reg):
    c = client([ADMIN])
    pine = f"Pine{reg}"
    assert _add(c, pine, "a sawmill").status_code == 303
    assert _add(c, f"Cedar {reg}", f"a Fir{reg} firm", ref=True).status_code == 303
    cid, *_, v = _row(reg, pine)
    r = c.post(f"/admin/clients/{cid}", data={"name": pine, "aliases": f"Fir{reg}", "anonymised_label": "a sawmill", "v": v})
    assert r.status_code == 400 and f"Cedar {reg}" in r.json()["detail"] and _row(reg, pine)[2] == []


def test_label_recheck_leaves_other_saves_alone(reg):
    c = client([ADMIN])
    oak = f"Oak {reg}"
    assert _add(c, oak, "a joinery").status_code == 303
    cid, *_, v = _row(reg, oak)
    assert c.post(f"/admin/clients/{cid}", data={"name": oak, "anonymised_label": "a carpenter", "v": v},
                  follow_redirects=False).status_code == 303
    elm = f"Elm {reg}"
    assert _add(c, elm, "a nursery", ref=True).status_code == 303
    assert _add(c, f"Birch {reg}", f"an {elm} unit", ref=True).status_code == 303
    cid, *_, v = _row(reg, elm)  # referenceable: its name may appear in another label
    assert c.post(f"/admin/clients/{cid}", data={"name": elm, "anonymised_label": "a grower", "referenceable": "on",
                                                 "v": v}, follow_redirects=False).status_code == 303


def test_alias_with_comma_is_one_alias(reg):
    c = client([ADMIN])
    assert c.post("/admin/clients", data={"name": f"SJ {reg}", "aliases": f"Smith, Jones Partners {reg}\r\nSJC",
                                          "anonymised_label": "a law firm"}, follow_redirects=False).status_code == 303
    (cid, name, aliases, label, ref, logo, v), = find(reg)
    assert aliases == [f"Smith, Jones Partners {reg}", "SJC"]
    assert c.post(f"/admin/clients/{cid}", data={"name": name, "aliases": "\n".join(aliases), "anonymised_label": label,
                                                 "v": v}, follow_redirects=False).status_code == 303
    assert find(reg)[0][2] == aliases
    row = C(name, aliases, label)
    assert an.apply(f"met Smith, Jones Partners {reg} today", [row]) == "met a law firm today"
    assert an.apply(f"Jones Partners {reg}", [row]) == f"Jones Partners {reg}"


def test_delete_only_referenceable_unlinked(reg):
    c = client([ADMIN])
    for n, ref in ((f"Pub {reg}", {"referenceable": "on"}), (f"Priv {reg}", {})):
        assert c.post("/admin/clients", data={"name": n, "anonymised_label": "a firm", **ref}).status_code == 200
    rows = {r[1]: r for r in find(reg)}
    pub, priv = rows[f"Pub {reg}"], rows[f"Priv {reg}"]
    d = lambda r, v=None, cl=c: cl.post(f"/admin/clients/{r}/delete", data={"v": v}, follow_redirects=False)  # noqa: E731
    assert d(pub[0], pub[6], client([REV])).status_code == 403
    r = d(priv[0], priv[6])
    assert r.status_code == 400 and "non-referenceable" in r.text and len(find(reg)) == 2
    assert d(pub[0], "stale").status_code == 409
    assert d(999999999, "x").status_code == 404
    page = c.get("/admin/clients").text
    assert f'action="/admin/clients/{pub[0]}/delete"' in page and f'action="/admin/clients/{priv[0]}/delete"' not in page
    assert d(pub[0], pub[6]).status_code == 303
    assert [r[1] for r in find(reg)] == [f"Priv {reg}"]


def test_delete_refused_for_linked_client(make, reg):  # noqa: F811
    with db.connect() as conn:
        client_id = conn.execute("insert into clients(name, aliases, anonymised_label, referenceable) "
                                 "values (%s, '{}', 'a firm', true) returning id", (f"Hooli {reg}",)).fetchone()[0]
    cid = make()
    try:
        with db.connect() as conn:
            conn.execute("update cases set client_id=%s where id=%s", (client_id, cid))
        r = client([ADMIN]).post(f"/admin/clients/{client_id}/delete", data={"v": find(reg)[0][6]}, follow_redirects=False)
        assert r.status_code == 400 and "case(s) use this client" in r.text
        assert len(find(reg)) == 1
        with db.connect() as conn:
            assert conn.execute("select client_id from cases where id=%s", (cid,)).fetchone()[0] == client_id
    finally:
        with db.connect() as conn:
            conn.execute("update cases set client_id=null where client_id=%s", (client_id,))


def test_label_may_not_contain_another_clients_protected_name(reg):
    c = client([ADMIN])
    c.post("/admin/clients", data={"name": f"Globex {reg}", "anonymised_label": "a manufacturer"})
    r = c.post("/admin/clients", data={"name": f"Acme {reg}", "anonymised_label": f"a Globex {reg} supplier"})
    assert r.status_code == 400 and "another client" in r.text


def test_resolve_exact_only():
    reg = [dict(id=1, **C("Acme Bank", ["Acme"])), dict(id=2, **C("Globex", ref=True))]
    assert an.resolve("ACME-bank", reg) == 1 and an.resolve("acme", reg) == 1
    assert an.resolve("Acme Insurance", reg) is None and an.resolve("", reg) is None and an.resolve(None, reg) is None


def test_name_or_alias_shared_with_another_client_refused(reg):
    c = client([ADMIN])
    c.post("/admin/clients", data={"name": f"Insurer {reg}", "aliases": f"Acme {reg}", "anonymised_label": "an insurer"})
    r = c.post("/admin/clients", data={"name": f"acme-{reg}", "anonymised_label": "a bank"})
    assert r.status_code == 400 and "already used" in r.text
