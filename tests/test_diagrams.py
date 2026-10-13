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
    icc = b"\x00" * 128 + b"desc Some ICC Client Ltd"  # a profile whose description is client text
    for fmt in ("PNG", "JPEG"):
        cid, gid = make(encode(fmt, exif=exif.tobytes(), icc_profile=icc) + b"trailing")
        diagrams.process(gid)
        entry, stored, png = state(cid, gid)
        assert (entry["status"], entry["width"], entry["height"], entry["reason"]) == ("ready", 40, 20, "")
        assert stored is None
        im = Image.open(BytesIO(bytes(png)))
        assert im.format == "PNG" and not im.getexif() and "icc_profile" not in im.info
        assert bytes(png).endswith(b"IEND\xaeB`\x82")
        assert b"Some Owner" not in bytes(png) and b"Some ICC Client" not in bytes(png)


def test_exif_orientation_is_applied(make):
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees to display
    cid, gid = make(encode("JPEG", (40, 20), exif=exif.tobytes()))
    diagrams.process(gid)
    entry, _, png = state(cid, gid)
    assert (entry["width"], entry["height"]) == (20, 40) and Image.open(BytesIO(bytes(png))).size == (20, 40)


def test_decoder_crash_rejects_the_diagram(make, monkeypatch):
    cid, gid = make(encode("PNG"))
    monkeypatch.setattr(diagrams, "_decode", lambda raw: (_ for _ in ()).throw(SyntaxError("boom")))
    diagrams.process(gid)
    entry, raw, png = state(cid, gid)
    assert (entry["status"], entry["reason"]) == ("rejected", diagrams.UNREADABLE) and raw is None and png is None


def test_case_gone_is_a_noop(make):
    cid, gid = make(encode("PNG"))
    with db.connect() as c:
        c.execute("delete from cases where id=%s", (cid,))
    diagrams.process(gid)  # no error


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


# --- images inside a docx/pptx (#212): the zip is hostile ---
import os
import struct
import zipfile
import zlib


def noise_png(side=500):
    b = BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(b, "PNG")
    return b.getvalue()


def bomb_png(side=9000):
    """A valid PNG header for a 9000 x 9000 image whose pixels are all zero: ~250 KB, 243 MB decoded."""
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))
    z, out = zlib.compressobj(9), []
    row = b"\x00" * (1 + side * 3)
    for _ in range(side):
        out.append(z.compress(row))
    out.append(z.flush())
    return (diagrams.MAGIC[0] + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", b"".join(out)) + chunk(b"IEND", b""))


def pack(entries, name="x.docx"):
    b = BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in entries.items():
            z.writestr(n, d)
    return b.getvalue()


def test_good_image_found_and_small_or_fake_ones_filtered():
    good = noise_png(500)
    z = pack({"word/media/a.png": good, "word/media/logo.png": noise_png(200),   # 120 KB but only 200 px
              "word/media/fake.png": b"A" * 40_000,                                # not an image
              "word/media/junk.png": diagrams.MAGIC[0] + b"J" * 40_000,            # magic, no image
              "word/media/tiny.png": good[:100], "word/other/b.png": good})       # too small; wrong folder
    got = diagrams.document_images_from(z, "Case.DOCX")
    assert len(got) == 1 and got[0][1:3] == (500, 500) and got[0][0].startswith(diagrams.MAGIC[0]) and got[0][3] == ""
    assert diagrams.document_images_from(z, "case.txt") == [] and diagrams.document_images_from(z, "case.pdf") == []


def test_bomb_is_refused_and_does_not_hide_a_good_image():
    z = pack({"word/media/bomb.png": bomb_png(), "word/media/ok.png": noise_png(450)})
    got = diagrams.document_images_from(z, "x.docx")
    assert [g[1:3] for g in got] == [(450, 450)]


def test_oversized_entry_is_skipped_before_it_is_read(monkeypatch):
    z = pack({"word/media/huge.png": diagrams.MAGIC[0] + b"\0" * (diagrams.MAX_BYTES + 10), "word/media/ok.png": noise_png(450)})
    opened = []
    real = zipfile.ZipFile.open
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda self, n, *a, **k: (opened.append(getattr(n, "filename", n)), real(self, n, *a, **k))[1])
    assert [g[1:3] for g in diagrams.document_images_from(z, "x.docx")] == [(450, 450)]
    assert "word/media/huge.png" not in opened and "word/media/ok.png" in opened


def test_entry_count_past_the_cap_reads_nothing(monkeypatch):
    monkeypatch.setattr(diagrams, "MAX_ENTRIES", 5)
    z = pack({**{f"word/other/{i}.txt": b"x" for i in range(6)}, "word/media/ok.png": noise_png(450)})
    assert diagrams.document_images_from(z, "x.docx") == []


def test_at_most_ten_largest_first_and_corrupt_zip_is_empty():
    z = pack({f"ppt/media/{i}.png": noise_png(420 + i * 10) for i in range(12)})
    got = diagrams.document_images_from(z, "x.pptx")
    assert len(got) == diagrams.MAX_FOUND and got[0][1] == 530 and [g[1] for g in got] == sorted((g[1] for g in got), reverse=True)
    assert diagrams.document_images_from(b"PK\x03\\x04 not a zip", "x.docx") == [] and diagrams.document_images_from(b"", "x.pptx") == []


def test_docx_alt_text_hint_is_anonymised_and_cut():
    import docx
    d = docx.Document()
    run = d.add_paragraph().add_run()
    run.add_picture(BytesIO(noise_png(500)))
    pr = run._r.xpath(".//wp:docPr")[0]
    pr.set("descr", "Target architecture for Zorpale Bank " + "x" * 400)
    out = BytesIO()
    d.save(out)
    clients = [{"name": "Zorpale Bank", "aliases": [], "anonymised_label": "a retail bank", "referenceable": False}]
    (png, w, h, hint), = diagrams.document_images_from(out.getvalue(), "d.docx", clients)
    assert "Zorpale" not in hint and hint.startswith("Target architecture for a retail bank") and len(hint) <= diagrams.MAX_CAPTION
    assert "Zorpale" in diagrams.document_images_from(out.getvalue(), "d.docx")[0][3]  # no registry, no anonymising


def test_pptx_hint_falls_back_to_the_slide_title_and_filename_alt_text_is_ignored():
    import pptx
    prs = pptx.Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Payments platform overview"
    slide.shapes.add_picture(BytesIO(noise_png(500)), 0, 0)  # python-pptx fills descr with "image.png"
    out = BytesIO()
    prs.save(out)
    (_, _, _, hint), = diagrams.document_images_from(out.getvalue(), "d.pptx")
    assert hint == "Payments platform overview"
