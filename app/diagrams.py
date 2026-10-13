"""Case diagrams: reviewer uploads (#200) and images found in a document's own docx/pptx (#212).

The web task only stores a raw upload; process() decodes it. document_images_from() is worker-only (it decodes).
"""
import hashlib
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

from psycopg.types.json import Jsonb

from app import anonymise, db, office

MAX_BYTES = 5 * 1024 * 1024
MAX_SIDE = 8000
MAX_PIXELS = 16_000_000
MAX_PER_CASE = 8
MAX_CAPTION = 200
MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
NOT_IMAGE = "not a PNG or JPEG"
TOO_LARGE = "too large: at most 8,000 px a side and 16 megapixels"
UNREADABLE = "the image could not be read"


def _decode(raw: bytes) -> tuple[bytes | None, int, int, str]:
    """(png, width, height, "") or (None, 0, 0, fixed reason). Never returns exception text."""
    from PIL import Image, ImageOps  # not at module level: the web task must not load Pillow for an upload
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(BytesIO(raw))
        if im.format not in ("PNG", "JPEG"):
            return None, 0, 0, NOT_IMAGE
        w, h = im.size
        if max(w, h) > MAX_SIDE or w * h > MAX_PIXELS:  # before load(): a bomb is never decoded
            return None, 0, 0, TOO_LARGE
        im.load()
        im = ImageOps.exif_transpose(im)  # rotated photos come out upright; the orientation tag goes with the EXIF
        im = im.convert("RGBA" if "A" in im.getbands() or "transparency" in im.info else "RGB")
        im.info = {}  # convert() keeps info, and the PNG writer would write icc_profile (client text can sit in it)
        out = BytesIO()
        im.save(out, "PNG")  # a fresh encode drops EXIF, text chunks, ICC and trailing data
        return out.getvalue(), *im.size, ""
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
        locked = conn.execute("select data from cases where id=%s for update", (case_id,)).fetchone()
        if not locked:
            return
        data = locked[0]
        row = conn.execute("select raw from diagrams where id=%s and case_id=%s for update", (diagram_id, case_id)).fetchone()
        if not row or row[0] is None:
            return
        try:
            png, w, h, reason = _decode(bytes(row[0]))
        except Exception as e:  # whatever the decoder raised: reject, never leave the diagram pending forever
            print(f"diagram {diagram_id} decoder error: {type(e).__name__}", flush=True)  # class only, as worker.py
            png, w, h, reason = None, 0, 0, UNREADABLE
        entries = data.get("diagrams", [])
        entry = next((d for d in entries if d["id"] == diagram_id), None)
        if entry is None:  # removed while processing
            conn.execute("delete from diagrams where id=%s", (diagram_id,))
            return
        entry.update(status="ready" if png else "rejected", reason=reason, width=w, height=h)
        # SQL writes data, so VERSION (md5 of data) changes for a page loaded while this was pending
        conn.execute("update cases set data = jsonb_set(data, '{diagrams}', %s) where id=%s", (Jsonb(entries), case_id))
        conn.execute("update diagrams set png=%s, raw=null where id=%s", (png, diagram_id))


# --- images inside a document (#212). The zip is untrusted: sizes are checked before anything is read,
# nothing is extracted to disk, and every image goes through _decode() (a fresh PNG, no metadata, bombs refused).
MIN_BYTES = 30_000       # a logo or icon is smaller
MIN_SIDE = 400
MAX_ENTRIES = 5000       # a zip with more entries is not read at all
MAX_DECODED = 30         # the largest entries tried, whatever the zip holds
MAX_FOUND = 10
MAX_XML = 5 * 1024 * 1024
VEC_MIN_BYTES = 2_000    # EMF/WMF are compact: below this it is a bullet or a line
MAX_VECTORS = 10
VEC_SCAN = 100           # the largest media entries whose first bytes are looked at
VEC_TIMEOUT = 30         # seconds, one soffice run per document
VEC_WAIT = 5             # seconds to wait for a soffice slot
CROP_MARGIN = 8
MAX_XML_TOTAL = 20 * 1024 * 1024  # all hint parts of one document; past it the images are kept without hints
MAX_SLIDES = 200
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_FILENAME = re.compile(r"\.(?:png|jpe?g|gif|bmp|tiff?|emf|wmf|svg)$", re.I)  # editors fill alt text with the file name


def _xml(zf: zipfile.ZipFile, name: str, budget: list[int]):
    """A small XML part as an element tree root, or None. OOXML never has a DTD, so one is refused,
    and neither is a part with a NUL byte (UTF-16 would hide the DOCTYPE test). `budget` is the bytes left for the document."""
    try:
        info = zf.getinfo(name)
        if info.file_size > MAX_XML or info.file_size > budget[0]:
            return None
        budget[0] -= info.file_size
        data = zf.open(info).read(MAX_XML + 1)
        if len(data) > MAX_XML or b"\0" in data or b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            return None
        return ET.fromstring(data)
    except Exception:  # noqa: BLE001 - a bad part loses its hints, never the images
        return None


def _targets(zf, rels_name: str, base: str, budget: list[int]) -> dict[str, str]:
    """{relationship id: part name} for one .rels file; `base` is the folder of the part it belongs to."""
    root = _xml(zf, rels_name, budget)
    out = {}
    for rel in (root.iter(_REL) if root is not None else ()):
        t = rel.get("Target") or ""
        if rel.get("Id") and t and rel.get("TargetMode") != "External":
            out[rel.get("Id")] = posixpath.normpath(t.lstrip("/") if t.startswith("/") else posixpath.join(base, t))
    return out


def _alt(descr: str | None) -> str:
    descr = " ".join((descr or "").split())
    return "" if _FILENAME.search(descr) else descr


def _hints(zf, ext: str) -> dict[str, str]:
    """{media part name: alt text, or the slide title for a pptx}. Untrusted text: cut and anonymised by the caller."""
    out: dict[str, str] = {}
    budget = [MAX_XML_TOTAL]
    if ext == ".docx":
        rels, root = _targets(zf, "word/_rels/document.xml.rels", "word", budget), _xml(zf, "word/document.xml", budget)
        for box in (root.iter() if root is not None else ()):
            if box.tag in (_WP + "inline", _WP + "anchor"):
                blip = next(box.iter(_A + "blip"), None)
                pr = box.find(_WP + "docPr")
                target = rels.get(blip.get(_EMBED)) if blip is not None else None
                if target and pr is not None and _alt(pr.get("descr")):
                    out.setdefault(target, _alt(pr.get("descr")))
        return out
    slides = sorted((n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.findall(r"\d+", n)[0]))[:MAX_SLIDES]
    for name in slides:
        root = _xml(zf, name, budget)
        if root is None:
            continue
        rels = _targets(zf, f"ppt/slides/_rels/{posixpath.basename(name)}.rels", "ppt/slides", budget)
        title = ""
        for sp in root.iter(_P + "sp"):
            ph = next(sp.iter(_P + "ph"), None)
            if ph is not None and ph.get("type") in ("title", "ctrTitle"):
                title = " ".join(" ".join(t.text or "" for t in sp.iter(_A + "t")).split())
                break
        for pic in root.iter(_P + "pic"):
            blip = next(pic.iter(_A + "blip"), None)
            pr = next(pic.iter(_P + "cNvPr"), None)
            target = rels.get(blip.get(_EMBED)) if blip is not None else None
            hint = _alt(pr.get("descr")) if pr is not None else ""
            if target and (hint or title):
                out.setdefault(target, hint or title)
    return out


def _vector_ext(head: bytes) -> str | None:
    """"emf" or "wmf" by magic bytes only; the entry name is never trusted."""
    if head[:4] == b"\x01\x00\x00\x00" and head[40:44] == b" EMF":
        return "emf"
    if head[:4] in (b"\xd7\xcd\xc6\x9a", b"\x01\x00\x09\x00", b"\x02\x00\x09\x00"):
        return "wmf"
    return None


def _crop(raw: bytes) -> bytes | None:
    """soffice renders a drawing onto a whole page: crop to the content plus a margin, or None
    when the page is blank or the content is under MIN_SIDE on its short side."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        im = Image.open(BytesIO(raw))
        if im.format != "PNG" or max(im.size) > MAX_SIDE or im.size[0] * im.size[1] > MAX_PIXELS:
            return None
        im.load()
        rgba = im.convert("RGBA")
        page = Image.new("RGB", rgba.size, "white")
        page.paste(rgba, mask=rgba.getchannel("A"))  # transparent counts as white paper
        from PIL import ImageOps
        box = ImageOps.invert(page.convert("L")).getbbox()
        if not box or min(box[2] - box[0], box[3] - box[1]) < MIN_SIDE:
            return None
        m = CROP_MARGIN
        out = BytesIO()
        page.crop((max(box[0] - m, 0), max(box[1] - m, 0), min(box[2] + m, page.width), min(box[3] + m, page.height))).save(out, "PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001 - a hostile render loses only itself
        return None


def _vector_candidates(zf, infos, prefix: str) -> list[tuple] | None:
    """[(info, png, w, h, sha256 of the entry)] from the EMF/WMF entries, converted in one soffice run.
    [] when there are none; None when the conversion failed (busy, timeout, exit, anything): worth trying again."""
    try:
        eligible = sorted((i for i in infos if i.filename.startswith(prefix) and not i.is_dir()
                           and VEC_MIN_BYTES <= i.file_size <= MAX_BYTES), key=lambda i: -i.file_size)[:VEC_SCAN]
        files, by_name = {}, {}
        for info in eligible:
            if len(files) >= MAX_VECTORS:
                break
            try:
                with zf.open(info) as f:
                    ext = _vector_ext(f.read(48))
                raw = b""
                if ext:
                    with zf.open(info) as f:
                        raw = f.read(MAX_BYTES + 1)  # bounded whatever the declared size said
            except Exception:  # noqa: BLE001 - one bad entry (a bad CRC) loses only itself, as in the raster loop
                continue
            if ext and len(raw) <= MAX_BYTES:
                key = f"v{len(files)}"  # distinct stems: soffice names the output <stem>.png
                files[f"{key}.{ext}"] = raw
                by_name[f"{key}.{ext}"] = (info, hashlib.sha256(raw).hexdigest())
        if not files:
            return []
        outs = office.convert(files, "png", VEC_TIMEOUT, VEC_WAIT, limit=MAX_BYTES)
        found = []
        for fname, png_raw in outs.items():
            cropped = _crop(png_raw)
            png, w, h, _ = _decode(cropped) if cropped else (None, 0, 0, "")
            if png and min(w, h) >= MIN_SIDE:
                found.append((by_name[fname][0], png, w, h, by_name[fname][1]))
        return found
    except Exception as e:  # noqa: BLE001 - the raster candidates stay; the class only is logged
        print(f"vector diagrams skipped: {type(e).__name__}", flush=True)
        return None


def document_images_from(data: bytes, name: str, clients=(), meta: dict | None = None) -> list[tuple[bytes, int, int, str]]:
    """Candidate diagrams inside a .docx or .pptx: [(png, width, height, hint)], largest first, at most MAX_FOUND.

    Never raises: a bad zip gives []. `clients` anonymises the hint (alt text can name a client).
    `meta`, if given, is filled in: "src_hashes" (sha256 of each image's original entry, parallel to the result)
    and "vector_failed" (the EMF/WMF conversion failed, so the caller should read the document again later).
    """
    meta = {} if meta is None else meta
    ext = posixpath.splitext(name.lower())[1]
    prefix = {".docx": "word/media/", ".pptx": "ppt/media/"}.get(ext)
    if not prefix:
        return []
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                return []
            big = sorted((i for i in infos if i.filename.startswith(prefix) and not i.is_dir()
                          and MIN_BYTES <= i.file_size <= MAX_BYTES),  # the declared size, before any read
                         key=lambda i: -i.file_size)[:MAX_DECODED]
            found = []
            for info in big:
                if len(found) >= MAX_FOUND:
                    break  # enough: the rest is not decoded
                try:
                    with zf.open(info) as f:
                        raw = f.read(MAX_BYTES + 1)  # bounded whatever the declared size said
                    if len(raw) > MAX_BYTES or not raw.startswith(MAGIC):
                        continue
                    png, w, h, _ = _decode(raw)
                except Exception:  # noqa: BLE001 - one bad entry (a lying size, a bad CRC) loses only itself
                    continue
                if png and min(w, h) >= MIN_SIDE:
                    found.append((info, png, w, h, hashlib.sha256(raw).hexdigest()))
            if len(found) < MAX_FOUND:
                vectors = _vector_candidates(zf, infos, prefix)
                meta["vector_failed"] = vectors is None
                found = sorted(found + (vectors or []), key=lambda f: -f[2] * f[3])
            hints = _hints(zf, ext) if found else {}
        out = []
        meta["src_hashes"] = [f[4] for f in found[:MAX_FOUND]]
        for info, png, w, h, _ in found[:MAX_FOUND]:
            # control and invisible characters go first: a zero-width space inside a name would hide it from the anonymiser
            hint = "".join(c for c in " ".join(hints.get(info.filename, "").split()) if c.isprintable())
            hint = (anonymise.apply(hint, clients) if clients else hint)[:MAX_CAPTION].strip()
            out.append((png, w, h, hint))
        return out
    except Exception:  # noqa: BLE001 - a hostile or broken file must never fail ingest
        return []
