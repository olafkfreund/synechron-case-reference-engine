"""Reviewer-uploaded case diagrams (#200). The web task only stores the raw upload; process() decodes it."""
from io import BytesIO

from psycopg.types.json import Jsonb

from app import db

MAX_BYTES = 5 * 1024 * 1024
MAX_SIDE = 8000
MAX_PIXELS = 16_000_000
MAX_PER_CASE = 5
MAX_CAPTION = 200
MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
NOT_IMAGE = "not a PNG or JPEG"
TOO_LARGE = "too large: at most 8,000 px a side and 16 megapixels"
UNREADABLE = "the image could not be read"


def _decode(raw: bytes) -> tuple[bytes | None, int, int, str]:
    """(png, width, height, "") or (None, 0, 0, fixed reason). Never returns exception text."""
    from PIL import Image  # not at module level: the web task must not load Pillow for an upload
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(BytesIO(raw))
        if im.format not in ("PNG", "JPEG"):
            return None, 0, 0, NOT_IMAGE
        w, h = im.size
        if max(w, h) > MAX_SIDE or w * h > MAX_PIXELS:  # before load(): a bomb is never decoded
            return None, 0, 0, TOO_LARGE
        im.load()
        im = im.convert("RGBA" if "A" in im.getbands() or "transparency" in im.info else "RGB")
        out = BytesIO()
        im.save(out, "PNG")  # a fresh encode drops EXIF, text chunks, ICC and trailing data
        return out.getvalue(), w, h, ""
    except Image.DecompressionBombError:
        return None, 0, 0, TOO_LARGE
    except (Image.UnidentifiedImageError, OSError, ValueError):
        return None, 0, 0, UNREADABLE


def process(diagram_id: int) -> None:
    with db.connect() as conn:
        # lock order: the case row, then the diagram row (the review routes lock the case first too)
        row = conn.execute("select case_id from diagrams where id=%s", (diagram_id,)).fetchone()
        if not row:
            return
        case_id = row[0]
        data = conn.execute("select data from cases where id=%s for update", (case_id,)).fetchone()[0]
        row = conn.execute("select raw from diagrams where id=%s and case_id=%s for update", (diagram_id, case_id)).fetchone()
        if not row or row[0] is None:
            return
        png, w, h, reason = _decode(bytes(row[0]))
        entries = data.get("diagrams", [])
        entry = next((d for d in entries if d["id"] == diagram_id), None)
        if entry is None:  # removed while processing
            conn.execute("delete from diagrams where id=%s", (diagram_id,))
            return
        entry.update(status="ready" if png else "rejected", reason=reason, width=w, height=h)
        # SQL writes data, so VERSION (md5 of data) changes for a page loaded while this was pending
        conn.execute("update cases set data = jsonb_set(data, '{diagrams}', %s) where id=%s", (Jsonb(entries), case_id))
        conn.execute("update diagrams set png=%s, raw=null where id=%s", (png, diagram_id))
