import uuid
from io import BytesIO

from PIL import Image

from app import db, research as rs
from tests.test_research import cleanup, make_row, web  # noqa: F401

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
