import uuid
from io import BytesIO

from PIL import Image

from app import db, research as rs
from tests.test_research import cleanup, env, make_row, reg, web  # noqa: F401

PNG_HEADERS = {"content-type": "image/png"}


def img(side=500, fmt="PNG"):
    b = BytesIO()
    Image.new("RGB", (side, side), "red").save(b, fmt)
    return b.getvalue()


def stored(rid):
    with db.connect() as c:
        return c.execute("select page_url, image_url, alt, width, height from research_images where research_id=%s order by id",
                         (rid,)).fetchall()


def setup(web, page, host="docs.example"):  # noqa: F811
    web.results = [f"https://{host}/guide"]
    web.html(host, "/robots.txt", "")
    web.html(host, "/guide", page)


def test_images_are_found_fetched_decoded_and_stored(web, cleanup):  # noqa: F811
    setup(web, '<figure><img src="/a.png"><figcaption>**Zor**pale chart</figcaption></figure>'
               '<img src="https://cdn.example/b.jpg" alt="b">')
    web.pages[("docs.example", "/a.png")] = (200, PNG_HEADERS, img())
    web.pages[("cdn.example", "/b.jpg")] = (200, {"content-type": "image/jpeg"}, img(450, "JPEG"))
    web.html("cdn.example", "/robots.txt", "")
    rid = make_row(f"research test {uuid.uuid4().hex}")
    rs.run(rid)
    assert stored(rid) == [("https://docs.example/guide", "https://docs.example/a.png", "Zorpale chart", 500, 500),
                           ("https://docs.example/guide", "https://cdn.example/b.jpg", "b", 450, 450)]
    assert ("docs.example", "/a.png") in web.seen


def test_skipped_candidates_and_caps(web, cleanup):  # noqa: F811
    tags = ['<img src="data:image/png;base64,AAAA">', '<img src="/v.svg">', '<img src="/small.png" width="100">',
            '<img src="/text.png">', '<img src="/tiny.png">'] + [f'<img src="/ok{i}.png">' for i in range(5)]
    setup(web, "".join(tags))
    web.pages[("docs.example", "/text.png")] = (200, {"content-type": "text/html"}, img())
    web.pages[("docs.example", "/tiny.png")] = (200, PNG_HEADERS, img(399))
    for i in range(5):
        web.pages[("docs.example", f"/ok{i}.png")] = (200, PNG_HEADERS, img())
    rid = make_row(f"research test {uuid.uuid4().hex}")
    rs.run(rid)
    assert [r[1].rsplit("/", 1)[1] for r in stored(rid)] == ["ok0.png", "ok1.png", "ok2.png"]  # 3 per page
    assert not {"/v.svg", "/small.png"} & {p for _, p in web.seen}


def test_nine_per_research(web, cleanup):  # noqa: F811
    web.results = [f"https://h{i}.example/guide" for i in range(4)]
    for i in range(4):
        web.html(f"h{i}.example", "/robots.txt", "")
        web.html(f"h{i}.example", "/guide", "".join(f'<img src="/{j}.png">' for j in range(3)))
        for j in range(3):
            web.pages[(f"h{i}.example", f"/{j}.png")] = (200, PNG_HEADERS, img())
    rid = make_row(f"research test {uuid.uuid4().hex}")
    rs.run(rid)
    assert len(stored(rid)) == 9


def test_image_ssrf_is_refused_and_the_research_still_succeeds(web, cleanup):  # noqa: F811
    setup(web, '<img src="http://169.254.169.254/m.png"><img src="/r.png"><img src="/ok.png">')
    web.pages[("docs.example", "/r.png")] = (302, {"location": "http://10.0.0.1/x.png"}, b"")
    web.pages[("docs.example", "/ok.png")] = (200, PNG_HEADERS, img())
    rid = make_row(f"research test {uuid.uuid4().hex}")
    rs.run(rid)
    with db.connect() as c:
        assert c.execute("select status from research where id=%s", (rid,)).fetchone()[0] == "done"
    assert [r[1] for r in stored(rid)] == ["https://docs.example/ok.png"]
    assert not any(h in ("169.254.169.254", "10.0.0.1") for h, _ in web.seen)


def test_a_cache_hit_has_no_images(web, cleanup):  # noqa: F811
    setup(web, '<img src="/a.png">')
    web.pages[("docs.example", "/a.png")] = (200, PNG_HEADERS, img())
    q = f"research test {uuid.uuid4().hex}"
    first, second = make_row(q), make_row(q)
    rs.run(first)
    rs.run(second)
    assert len(stored(first)) == 1 and stored(second) == []


def add_image(rid, alt="a chart", page="https://docs.example/guide", png=None):
    with db.connect() as c:
        c.execute("update research set status='done' where id=%s", (rid,))
        return c.execute("insert into research_images(research_id, page_url, image_url, alt, png, width, height) "
                         "values (%s,%s,'https://docs.example/a.png',%s,%s,500,500) returning id", (rid, page, alt, png or img())).fetchone()[0]


def test_thumbnails_and_route_are_owner_only(reg):  # noqa: F811
    from tests.test_research import as_user
    rid = make_row("research test own", created_by="u1")
    iid = add_image(rid)
    c = as_user("u1")
    r = c.get(f"/research/{rid}/images/{iid}.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "private, no-store"
    page = c.get(f"/research/{rid}").text
    assert f"/research/{rid}/images/{iid}.png" in page and "a chart" in page and "Copyright of its owner" in page
    assert as_user("u2").get(f"/research/{rid}/images/{iid}.png").status_code == 404
    assert as_user("u2").get(f"/research/{rid}").status_code == 404
    assert c.get(f"/research/{rid + 1}/images/{iid}.png").status_code == 404  # an image of another row


def test_an_image_naming_a_protected_client_is_hidden_everywhere(reg):  # noqa: F811
    from tests.test_research import as_user
    rid = make_row("research test blocked", created_by="u1")
    iid = add_image(rid, page=f"https://docs.example/{reg}")  # a url is not rewritten, so it is hidden
    named = add_image(rid, alt=f"{reg} architecture")  # an alt is anonymised, then shown
    ok = add_image(rid, alt="plain chart")
    c = as_user("u1")
    assert c.get(f"/research/{rid}/images/{iid}.png").status_code == 404
    page = c.get(f"/research/{rid}").text
    assert reg not in page and f"images/{iid}.png" not in page and f"images/{ok}.png" in page and f"images/{named}.png" in page


# --- rights through attach ---------------------------------------------------------------------------

from app import diagrams  # noqa: E402
from tests.test_render import doc_text, gen, prev  # noqa: E402
from tests.test_research_claims import BASE, case_with, echo, finish, sent  # noqa: E402,F401
from tests.test_review import DOCS, make  # noqa: E402,F401
from tests.test_search import approved  # noqa: E402,F401

PAGE = {"url": "https://docs.example/guide", "publisher": "docs.example", "title": "**Guide** to Kafka",
        "retrieved_at": "2026-10-01T00:00:00+00:00", "markdown": "m"}
OK = {"rights_ok_{i}": "on", "no_logo_{i}": "on", "basis_{i}": "Licensed CC-BY, see footer"}


def attach_form(c, cid, rid, iid, page=None, **over):
    import re
    t = c.get(f"/research/{page or rid}").text
    target = re.search(rf'<option value="({cid}:[0-9a-f]+)"', t).group(1)
    data = {"target": target, "research_id": rid, "img": iid, **{k.format(i=iid): v for k, v in OK.items()}}
    data.update({k.format(i=iid): v for k, v in over.items()})
    return c.post("/sources/attach", data={k: v for k, v in data.items() if v is not None}, follow_redirects=False)


def case_diagrams(cid):
    with db.connect() as c:
        return c.execute("select data->'diagrams' from cases where id=%s", (cid,)).fetchone()[0] or []


def reviewer_row(approved, reg, echo):  # noqa: F811
    from tests.test_research import as_user
    cid = approved(case_with())
    c = as_user("u1", ["g-rev", DOCS])
    loc = sent(c, cid)
    rid = int(loc.rsplit("/", 1)[1])
    finish(loc, [{**BASE, "quote": "q q q q", "url": PAGE["url"], "publisher": "docs.example"}], pages=[PAGE])  # the picker shows with claims
    return c, cid, rid, add_image(rid, alt="Kafka topology")


def test_confirmed_image_becomes_a_pending_diagram_with_a_credit_and_no_reviewer(approved, reg, echo):  # noqa: F811
    c, cid, rid, iid = reviewer_row(approved, reg, echo)
    assert attach_form(c, cid, rid, iid).status_code == 303
    (d,) = case_diagrams(cid)
    assert d["status"] == "pending" and d["caption"] == "Kafka topology"
    assert d["credit"] == "Image: Guide to Kafka, docs.example, https://docs.example/guide, retrieved 2026-10-01"
    assert "u1" not in str(d)
    with db.connect() as conn:
        assert conn.execute("select rights_by, rights_at is not null, rights_basis from research_images where id=%s", (iid,)).fetchone() \
            == ("u1", True, "Licensed CC-BY, see footer")
        assert conn.execute("select count(*) from jobs where kind='diagram' and payload->>'diagram_id'=%s", (str(d["id"]),)).fetchone()[0] == 1
    diagrams.process(d["id"])
    (d,) = case_diagrams(cid)
    assert d["status"] == "ready"
    with db.connect() as conn:  # the attach reopened the case; a reviewer approves it again
        conn.execute("update cases set status='approved', review_due=now() + interval '30 days' where id=%s", (cid,))
    md = gen([cid], "md").text
    assert "Kafka topology Image: Guide to Kafka, docs.example" in md and "u1" not in md
    assert "Image: Guide to Kafka" in prev([cid]).text
    assert "Image: Guide to Kafka" in doc_text(gen([cid], "docx").content)


def test_missing_confirmation_basis_or_wrong_owner_changes_nothing(approved, reg, echo):  # noqa: F811
    from tests.test_research import as_user
    c, cid, rid, iid = reviewer_row(approved, reg, echo)
    for over in ({"rights_ok_{i}": None}, {"no_logo_{i}": None}, {"basis_{i}": ""}, {"basis_{i}": "x" * 201}):
        assert attach_form(c, cid, rid, iid, **over).status_code == 400, over
    other = as_user("u2", ["g-rev", DOCS])  # a reviewer who does not own the research
    import re
    target = re.search(rf'<option value="({cid}:[0-9a-f]+)"', c.get(f"/research/{rid}").text).group(1)  # a valid token
    assert other.post("/sources/attach", data={"target": target, "research_id": rid, "img": iid, f"rights_ok_{iid}": "on",
                                               f"no_logo_{iid}": "on", f"basis_{iid}": "b"}).status_code == 404
    mine = make_row("research test mine", created_by="u1")
    assert attach_form(c, cid, mine, iid, page=rid).status_code == 404  # the image belongs to another research
    assert case_diagrams(cid) == []
    with db.connect() as conn:
        assert conn.execute("select rights_by from research_images where id=%s", (iid,)).fetchone()[0] is None


def test_only_a_reviewer_may_confirm_rights(approved, reg, echo):  # noqa: F811
    from tests.test_research import as_user
    c, cid, rid, iid = reviewer_row(approved, reg, echo)
    r = as_user("u1", ["g-user", DOCS]).post("/sources/attach", data={"target": f"{cid}:x", "research_id": rid, "img": iid})
    assert r.status_code == 403
    assert case_diagrams(cid) == []


def test_picker_fields_are_on_the_reviewer_page_only(approved, reg, echo):  # noqa: F811
    from tests.test_research import as_user
    c, cid, rid, iid = reviewer_row(approved, reg, echo)
    t = c.get(f"/research/{rid}").text
    assert f'name="basis_{iid}"' in t and f'name="rights_ok_{iid}"' in t and f'name="no_logo_{iid}"' in t
    assert 'name="basis_' not in as_user("u1", ["g-user", DOCS]).get(f"/research/{rid}").text
