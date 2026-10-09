import re
from html.parser import HTMLParser

import pytest

from app import db, search as sr
from tests.test_auth import ADMIN, REV, USER, client, env  # noqa: F401
from tests.test_review import DOCS, case_data, make  # noqa: F401
from tests.test_search import approved, data, picks_reply  # noqa: F401

TABS = {
    "admin": ["Find references", "Research", "Review queue", "Sources", "Client registry", "Model approvals", "Audit"],
    "reviewer": ["Find references", "Research", "Review queue"],
    "sales": ["Find references", "Research"],
}
GROUPS = {"admin": [ADMIN], "reviewer": [REV], "sales": [USER], "nobody": ["other"]}


def test_static_is_public(env):
    c = client()
    assert c.get("/static/portal.css").status_code == 200
    assert c.get("/static/fonts/ibm-plex-sans-400.woff2").status_code == 200


@pytest.mark.parametrize("role", TABS)
def test_landing_has_css_no_script_and_the_roles_tabs(env, role):
    r = client(GROUPS[role]).get("/")
    assert r.status_code == 200
    assert "/static/portal.css" in r.text and "<script" not in r.text
    nav = re.search(r'<nav class="site-nav".*?</nav>', r.text, re.S).group(0)
    assert re.findall(r">([^<]+)</a>", nav) == TABS[role]


def test_no_role_gets_no_landing(env):
    assert client(GROUPS["nobody"]).get("/").status_code == 403  # nothing to show, so no tabs either


def test_search_tab_is_current(env):
    r = client(GROUPS["sales"]).get("/search")
    assert re.search(r'href="/search" aria-current="page">Find references', r.text)
    assert r.text.count("aria-current") == 1


@pytest.mark.parametrize("path", ["/static/../main.py", "/static/%2e%2e/main.py", "/static/..%2fmain.py"])
def test_static_never_leaves_its_directory(env, path):  # noqa: F811
    assert client(None).get(path).status_code == 404


class Scan(HTMLParser):
    """Collects what the polish rules (#90) check: scripts, unlabelled named fields, docx buttons outside <details>."""

    def __init__(self):
        super().__init__()
        self.scripts, self.fors, self.fields, self.docx_outside = 0, set(), [], 0
        self.label_depth = self.details_depth = 0

    def handle_starttag(self, tag, a):
        a = dict(a)
        # inline handlers and javascript: links count as scripts
        self.scripts += sum(k.startswith("on") for k in a) + str(a.get("href", "")).strip().lower().startswith("javascript:")
        if tag == "script":
            self.scripts += 1
        elif tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.fors.add(a["for"])
        elif tag == "details":
            self.details_depth += 1
        elif tag in ("input", "select", "textarea") and a.get("name") and a.get("type") != "hidden":
            wrapped = self.label_depth > 0 and a.get("type") in ("checkbox", "radio", "submit")
            if not wrapped:
                self.fields.append((a.get("id"), a["name"], self.label_depth > 0))
        elif tag == "button" and a.get("value") == "docx" and not self.details_depth:
            self.docx_outside += 1

    def handle_endtag(self, tag):
        if tag == "label":
            self.label_depth -= 1
        elif tag == "details":
            self.details_depth -= 1


def scan(html):
    s = Scan()
    s.feed(html)
    return s


def unlabelled(s):
    return [n for i, n, wrapped in s.fields if not wrapped and i not in s.fors]


def pages(make, approved, monkeypatch):  # noqa: F811
    cid = approved()
    cid2 = approved(data(title="Second onboarding"))
    picks_reply(monkeypatch, sr.Pick(case_id=cid, reason="fits", tailored="Cut it."),
                sr.Pick(case_id=cid2, reason="also", tailored="Cut it too."))
    rid = make()
    admin, rev, user = client([ADMIN, DOCS]), client([REV, DOCS]), client([USER, DOCS])
    return {
        "home": admin.get("/"),
        "search": user.get("/search"),
        "search results": user.post("/search", data={"bid_text": "onboarding"}),
        "review list": rev.get("/review"),
        "review detail": rev.get(f"/review/{rid}"),
        "sources": admin.get("/admin/sources"),
        "clients": admin.get("/admin/clients"),
        "models": admin.get("/admin/models"),
        "audit": admin.get("/admin/audit"),
        "research": user.get("/research"),
    }


def test_every_page_has_no_script_and_labelled_fields(make, approved, monkeypatch):  # noqa: F811
    for name, r in pages(make, approved, monkeypatch).items():
        assert r.status_code == 200, name
        s = scan(r.text)
        assert s.scripts == 0, name
        assert unlabelled(s) == [], name


def test_two_results_have_one_visible_word_download_each_plus_download_all(make, approved, monkeypatch):  # noqa: F811
    r = pages(make, approved, monkeypatch)["search results"]
    assert r.text.count("Download all (Word)") == 1 and scan(r.text).docx_outside == 3  # 2 cases + all
    assert r.text.count("Download this case (Word)") == 0  # the Word button is the only one outside the panel


def test_client_rows_are_scanned_for_labels(env):  # noqa: F811
    db.init()
    with db.connect() as c:
        c.execute("insert into clients(name, anonymised_label, referenceable) values ('UI Test Client', 'a test bank', true)")
    try:
        r = client([ADMIN]).get("/admin/clients")
        s = scan(r.text)
        assert "UI Test Client" in r.text and "-aliases" in r.text
        assert s.scripts == 0 and unlabelled(s) == [] and sum(1 for i, *_ in s.fields if i and i.startswith("c") and i.endswith(("-name", "-aliases", "-label"))) == 3
    finally:
        with db.connect() as c:
            c.execute("delete from clients where name = 'UI Test Client'")
