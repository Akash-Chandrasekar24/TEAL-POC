"""INGEST (architecture 4.3 #4, cache layer C1): drawing files -> pages, every page a PNG.

- The file type comes from the BYTES, never from the extension (inputs\\image_2.jpg was a WebP, image_9.png an AVIF).
- A PDF becomes one page per PDF page, rendered at `dpi` (pypdfium2). A picture is one page; PNG is kept as it is, any other format becomes PNG.
- Pages are stored in the blob store and the list of a file's pages is cached under (INGEST_V, file sha, dpi): the same file is rendered once.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from app.cache import Cache, key, sha256
from app.llm.base import FilePart, LLMError

INGEST_V = 1                   # bump when the way a file becomes pages changes
PDF_DPI = 300
LAYER = "pages"
_MODEL_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}   # what every model client accepts as is


@dataclass
class Page:
    no: int                    # 1-based, across all files of the set
    source: str                # the file it came from
    source_page: int           # 1-based page inside that file
    sha: str                   # sha256 of `png`
    width: int
    height: int
    dpi: int | None            # render DPI for a PDF page, None for a picture
    png: bytes = field(repr=False)


def _is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def _to_png(data: bytes) -> bytes:
    try:
        im = Image.open(io.BytesIO(data))
        if im.format == "PNG":
            return data
        im.load()
    except (OSError, Image.DecompressionBombError) as ex:
        raise ValueError(f"not a picture this program can read ({type(ex).__name__})") from None
    buf = io.BytesIO()
    (im if im.mode in ("1", "L", "RGB", "RGBA") else im.convert("RGB")).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _render_pdf(data: bytes, dpi: int) -> list[bytes]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    out = []
    try:
        for i in range(len(doc)):
            im = doc[i].render(scale=dpi / 72).to_pil()
            buf = io.BytesIO()
            im.save(buf, "PNG", optimize=True)
            out.append(buf.getvalue())
    finally:
        doc.close()
    return out


def _file_pngs(data: bytes, dpi: int, cache: Cache | None, tenant: str) -> tuple[list[bytes], bool]:
    """(page PNGs, answered from the cache)."""
    k = key(INGEST_V, sha256(data), dpi)
    if cache is not None:
        shas = cache.get_json(tenant, LAYER, k)
        if shas is not None:
            blobs = [cache.get_blob(s) for s in shas]
            if all(b is not None for b in blobs):
                return blobs, True                                     # type: ignore[return-value]
    pngs = _render_pdf(data, dpi) if _is_pdf(data) else [_to_png(data)]
    if cache is not None:
        cache.put_json(tenant, LAYER, k, [cache.put_blob(p) for p in pngs], versions={"ingest": INGEST_V, "dpi": dpi})
    return pngs, False


def _kind(data: bytes) -> str:
    """The file type read from the bytes (what the trace reports; the extension is not trusted)."""
    if _is_pdf(data):
        return "PDF"
    try:
        return Image.open(io.BytesIO(data)).format or "unknown"
    except OSError:
        return "unreadable"


def load_pages(paths: list[str | Path], dpi: int = PDF_DPI, cache: Cache | None = None, tenant: str = "default") -> list[Page]:
    """Every page of every file, in order. Raises ValueError for a file that is neither a PDF nor a readable picture."""
    from app import trace
    tr = trace.current()
    pages: list[Page] = []
    for path in paths:
        data = Path(path).read_bytes()
        with tr.step("ingest", "file", name=Path(path).name, bytes=len(data), type=_kind(data), extension=Path(path).suffix.lower()) as out:
            pngs, cached = _file_pngs(data, dpi, cache, tenant)
            for i, png in enumerate(pngs, 1):
                w, h = Image.open(io.BytesIO(png)).size
                pages.append(Page(len(pages) + 1, str(path), i, sha256(png), w, h, dpi if _is_pdf(data) else None, png))
            out.update(pages=len(pngs), dpi=dpi if _is_pdf(data) else None, cached=cached,
                       sizes=[[p.width, p.height] for p in pages[-len(pngs):]], page_shas=[p.sha[:12] for p in pages[-len(pngs):]])
    return pages


def file_part(path: str | Path) -> FilePart:
    """A drawing file as a model input, with the media type read from its bytes: PNG / JPEG / WebP / PDF as they are, anything else as PNG."""
    data = Path(path).read_bytes()
    if _is_pdf(data):
        return FilePart(data, "application/pdf")
    try:
        fmt = Image.open(io.BytesIO(data)).format
    except OSError:
        raise LLMError(f"{Path(path).name} is neither a PDF nor a picture this program can read") from None
    if fmt in _MODEL_MIMES:
        return FilePart(data, _MODEL_MIMES[fmt])
    return FilePart(_to_png(data), "image/png")
