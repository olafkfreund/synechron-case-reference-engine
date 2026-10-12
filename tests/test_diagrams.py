import uuid
from io import BytesIO

import pytest
from PIL import Image
from psycopg.types.json import Jsonb

from app import db, diagrams
from app.schema import Sourced

TITLE = Sourced[str](value="T", source_quote="q").model_dump()


@pytest.fixture
def make():
    db.init()
    sids = []

    def make(raw, diagram_entry=True):
        """A case with one pending diagram holding `raw`; returns (case_id, diagram_id)."""
        with db.connect() as c:
            sid = c.execute("insert into sources(kind,name) values ('s3',%s) returning id", (uuid.uuid4().hex,)).fetchone()[0]
            sids.append(sid)
            did = c.execute("insert into documents(source_id,external_id,title,checksum,text) "
                            "values (%s,'k','d','x','t') returning id", (sid,)).fetchone()[0]
            cid = c.execute("insert into cases(document_id,data) values (%s,%s) returning id",
                            (did, Jsonb({"title": TITLE}))).fetchone()[0]
            gid = c.execute("insert into diagrams(case_id,raw,created_by) values (%s,%s,'t') returning id", (cid, raw)).fetchone()[0]
            if diagram_entry:
                c.execute("update cases set data = data || %s where id=%s", (Jsonb({"diagrams": [
                    {"id": gid, "caption": "c", "status": "pending", "reason": "", "width": 0, "height": 0}]}), cid))
            return cid, gid
    yield make
    with db.connect() as c:
        for sid in sids:
            c.execute("delete from sources where id=%s", (sid,))


def encode(fmt, size=(40, 20), **kw):
    b = BytesIO()
    Image.new("RGB", size, "red").save(b, fmt, **kw)
    return b.getvalue()


def state(cid, gid):
    with db.connect() as c:
        entry = c.execute("select data->'diagrams'->0 from cases where id=%s", (cid,)).fetchone()[0]
        raw, png = c.execute("select raw, png from diagrams where id=%s", (gid,)).fetchone()
    return entry, raw, png


def version(cid):
    with db.connect() as c:
        return c.execute("select md5(data::text) from cases where id=%s", (cid,)).fetchone()[0]


def test_png_and_jpeg_become_png_without_metadata(make):
    exif = Image.Exif()
    exif[0x013B] = "Some Owner"  # Artist
    for raw in (encode("PNG"), encode("JPEG", exif=exif.tobytes()) + b"trailing"):
        cid, gid = make(raw)
        diagrams.process(gid)
        entry, stored, png = state(cid, gid)
        assert (entry["status"], entry["width"], entry["height"], entry["reason"]) == ("ready", 40, 20, "")
        assert stored is None
        im = Image.open(BytesIO(bytes(png)))
        assert im.format == "PNG" and not im.getexif() and bytes(png).endswith(b"IEND\xaeB`\x82")
        assert b"Some Owner" not in bytes(png)


def test_pixel_bomb_rejected_before_decode(make, monkeypatch):
    made = [make(encode("PNG", size)) for size in ((9000, 10), (5000, 5000))]  # a side too long; too many pixels
    monkeypatch.setattr(Image.Image, "load", lambda self: pytest.fail("decoded"))
    for cid, gid in made:
        diagrams.process(gid)
        entry, raw, png = state(cid, gid)
        assert (entry["status"], entry["reason"]) == ("rejected", diagrams.TOO_LARGE)
        assert raw is None and png is None


@pytest.mark.parametrize("raw,reason", [
    (b"not an image at all", diagrams.UNREADABLE),
    (encode("GIF"), diagrams.NOT_IMAGE),
    (encode("PNG")[:30], diagrams.UNREADABLE),
])
def test_corrupt_or_wrong_format_rejected_with_fixed_reason(make, raw, reason):
    cid, gid = make(raw)
    diagrams.process(gid)
    entry, stored, png = state(cid, gid)
    assert (entry["status"], entry["reason"]) == ("rejected", reason)
    assert stored is None and png is None


def test_ready_changes_case_version(make):
    cid, gid = make(encode("PNG"))
    before = version(cid)
    diagrams.process(gid)
    assert version(cid) != before


def test_removed_while_processing_deletes_row(make):
    cid, gid = make(encode("PNG"), diagram_entry=False)
    diagrams.process(gid)
    with db.connect() as c:
        assert c.execute("select count(*) from diagrams where id=%s", (gid,)).fetchone()[0] == 0


def test_diagram_deleted_before_processing_is_a_noop(make):
    cid, gid = make(encode("PNG"))
    with db.connect() as c:
        c.execute("delete from diagrams where id=%s", (gid,))
    before = version(cid)
    diagrams.process(gid)
    assert version(cid) == before
