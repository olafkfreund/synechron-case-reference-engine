"""The reviewer gate and the allow-listed view (#249 phase 4)."""
import asyncio
import json
import uuid

import pytest
from pydantic import BaseModel

from app import db, main, mcp_server as ms
from app.mcp_server import AccessToken
from app.review import load
from tests.test_mcp_http import env  # noqa: F401
from tests.test_review import DOC, DOCS, make  # noqa: F401

CLIENT = "client-1"


class Out(BaseModel):
    ok: bool = True


@pytest.fixture
def gate(env, monkeypatch):  # noqa: F811
    db.init()
    made = []

    def call(roles=("user", "reviewer"), scp="refs.access refs.review", approval="live"):
        who = f"t-{uuid.uuid4()}"
        user = main.User(who, who, frozenset([DOCS]), frozenset(roles))
        claims = dict(scp=scp, azp=CLIENT, oid=who, _user=user)
        monkeypatch.setattr(ms, "get_access_token", lambda: AccessToken(token="t", client_id=CLIENT, scopes=scp.split(), claims=claims))
        with db.connect() as c:
            c.execute("delete from model_approvals where model = %s", (f"mcp-client:{CLIENT}",))
            if approval:
                c.execute("insert into model_approvals(model, data_class, approved_by, expires_at, revoked_by) values "
                          "(%s, 'confidential', 'admin', now() + %s::interval, %s)",
                          (f"mcp-client:{CLIENT}", "1 day" if approval == "live" else "-1 day", "admin" if approval == "revoked" else None))
        ran = []
        res = asyncio.run(ms._run(lambda u: (ran.append(1), (Out(), []))[1], "gate_probe", review=True))
        with db.connect() as c:
            rows = c.execute("select tool, outcome from tool_calls where user_key = %s", (who,)).fetchall()
        return res, rows, ran

    yield call
    with db.connect() as c:
        c.execute("delete from model_approvals where model = %s", (f"mcp-client:{CLIENT}",))


def test_the_gate_passes_with_all_three(gate):
    res, rows, ran = gate()
    assert not res.is_error and rows == [("gate_probe", "ok")] and ran


@pytest.mark.parametrize("kw, text", [
    (dict(roles=("user",)), "reviewer role required"),
    (dict(scp="refs.access"), "refs.review scope"),
    (dict(approval=None), "not approved to handle confidential data"),
    (dict(approval="expired"), "not approved to handle confidential data"),
    (dict(approval="revoked"), "not approved to handle confidential data"),
])
def test_each_gate_refusal_is_audited_once_and_the_tool_never_runs(gate, kw, text):
    res, rows, ran = gate(**kw)
    assert res.is_error and text in res.content[0].text
    assert rows == [("gate_probe", "denied")] and not ran


def test_view_has_only_the_allow_list(make):  # noqa: F811
    cid = make(text=DOC + " See https://example.com/secret and external key k.")
    from app.schema import ReferenceCase
    user = main.User("r", "r", frozenset([DOCS]), frozenset({"user", "reviewer"}))
    with db.connect() as c:
        case, _ = load(c, cid, user, c.execute("select md5(data::text) from cases where id=%s", (cid,)).fetchone()[0])
    view = ms.review_view(case, cid, "extracted", "Source doc", "vv")
    assert set(view) == {"id", "status", "basis", "document", "v", "fields", "prose", "notes", "completeness"}
    blob = json.dumps(view)
    assert "example.com" not in blob and "external key" not in blob and "http" not in blob
    f = {x["path"]: x for x in view["fields"]}
    assert f["title"]["state"] == "sourced" and f["industry"]["state"] == "unsourced" and f["summary"]["values"]["value"]
    assert view["notes"] == ["check the summary"] and view["prose"]["challenge"]["by_reviewer"] is True
    assert not any(x["path"].endswith(".new") for x in view["fields"])
