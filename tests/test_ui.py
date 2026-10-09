import re

import pytest

from tests.test_auth import ADMIN, REV, USER, client, env  # noqa: F401

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
