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
