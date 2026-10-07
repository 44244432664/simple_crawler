"""Content-aware exporters — the ``create_ebook`` flow module.

Task 5 of ``TASK/ai-integration_TASK.md`` (see ``PLAN/ai-integration_PLAN.md``).

This module implements the common export dispatcher, which turns the
normalized :class:`CrawlContext` state (``metadata``, ``volumes``,
``chapter_contents``, ``image_entries``, ``output_dir``) into final artifacts.

Design rules
------------
* Exact replacement.  Remote image tags are replaced by **exact raw-tag plus
  occurrence** matching against the image manifest — never by substring URL
  replacement.  Only ``ImageStatus.DOWNLOADED`` entries produce local refs.
* Local refs only where matching succeeded.  FAILED/missing images never leak
  a remote URL; they become an ``<img alt="img error">`` placeholder.
* Content-aware dispatch.  ``novel`` → EPUB/PDF (combined or per-volume);
  ``comic``/``gallery`` → CBZ/PDF/folder (per chapter / per gallery).
* Atomic writes.  Every artifact is written to a temporary path/stream and
  atomically moved into its final name only after success; on failure the
  temp output is removed and no final artifact is left behind.
* Deterministic naming.  Artifact basenames are sanitized from the safe title;
  collisions resolve with a numeric suffix.
* Readable novel PDF.  Rendered with fpdf2 + an embedded Unicode-capable font
  (DejaVu Serif), preserving headings, paragraphs, basic emphasis, volume/page
  breaks, and downloaded inline images.  Site CSS/scripts are ignored.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import zipfile
from html import escape
from urllib.parse import urlsplit

from api.chapter_crawl import _safe_filename
from api.contracts import (
    ArtifactResult,
    ContentType,
    CrawlContext,
    ExporterError,
    ImageManifestEntry,
    ImageStatus,
    IncompleteCrawlError,
    InvalidFlowError,
    OutputFormat,
    PackagingMode,
)
from api.flow import set_module_handler

try:  # pragma: no cover - optional third-party
    from ebooklib import epub
except Exception:  # pragma: no cover
    epub = None

try:  # pragma: no cover - optional third-party
    from fpdf import FPDF
except Exception:  # pragma: no cover
    FPDF = None

# Preferred Unicode-capable fonts for the readable novel PDF.  The first
# existing path wins; DejaVu is used because it covers a wide Unicode range.
# Preferred Unicode-capable fonts for the readable novel PDF.  The first pair
# of existing paths wins.  A CJK-capable font is preferred so that both Latin
# and non-Latin text can be embedded (required by the "Unicode-capable fonts"
# acceptance criterion).
_FONT_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)

_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_STRONG_TAGS = {"b", "strong"}
_EMPH_TAGS = {"i", "em", "u", "s", "sub", "sup", "code", "small"}


# ---------------------------------------------------------------------------
# Manifest replacement (exact raw-tag + occurrence)
# ---------------------------------------------------------------------------


_IMG_ERROR_TAG = '<img alt="img error">'
_IMAGE_SOURCE_ATTRS = ("src", "data-src", "data-original", "data-lazy-src")


def _replacement_plan(context: CrawlContext) -> dict[int, dict[str, list[str]]]:
    """Return exact raw-tag replacements in manifest occurrence order.

    A failed download, a manifest entry whose downloaded file has disappeared,
    or an incomplete manifest occurrence is intentionally rendered as the
    local, network-free ``img error`` placeholder.  It is important that this
    decision is made from the manifest rather than by URL substitution.
    """
    grouped: dict[int, list[ImageManifestEntry]] = {}
    for entry in context.image_entries:
        if entry.raw_tag:
            grouped.setdefault(entry.chapter_ordinal, []).append(entry)

    plan: dict[int, dict[str, list[str]]] = {}
    for ordinal, entries in grouped.items():
        for entry in sorted(entries, key=lambda item: item.occurrence):
            replacement = _IMG_ERROR_TAG
            if (
                entry.status is ImageStatus.DOWNLOADED
                and entry.filename
                and entry.replacement_tag
                and os.path.isfile(_image_abs_path(context, entry))
            ):
                replacement = entry.replacement_tag
            plan.setdefault(ordinal, {}).setdefault(entry.raw_tag, []).append(replacement)
    return plan


def _apply_replacement(html: str, repl_tags: dict[str, list[str]]) -> tuple[str, list[str]]:
    """Return ``(html, unresolved_raw_tags)`` after exact occurrence replacement.

    Occurrences of each distinct ``raw_tag`` are replaced by the corresponding
    manifest replacement tags in order.  If there are more occurrences than
    manifest entries, each extra tag becomes an ``img error`` placeholder (so
    no remote URL leaks into the artifact) and is recorded as unresolved.
    """
    if not repl_tags:
        return html, []
    unresolved: list[str] = []
    # Replace longest tags first so a shorter tag that is a substring of a
    # longer one does not consume that longer tag's occurrences.
    for raw_tag in sorted(repl_tags, key=len, reverse=True):
        occurrences = html.count(raw_tag)
        if occurrences == 0:
            continue
        replacements = repl_tags[raw_tag]
        repl_iter = iter(replacements)
        out: list[str] = []
        pos = 0
        replaced = 0
        while True:
            idx = html.find(raw_tag, pos)
            if idx == -1:
                out.append(html[pos:])
                break
            out.append(html[pos:idx])
            try:
                out.append(next(repl_iter))
                replaced += 1
            except StopIteration:
                out.append(_IMG_ERROR_TAG)
            pos = idx + len(raw_tag)
        html = "".join(out)
        if replaced < occurrences:
            unresolved.append(raw_tag)
    return html, unresolved


def _download_entry_index(context: CrawlContext) -> dict[int, list[ImageManifestEntry]]:
    """Index downloadable images by chapter ordinal (source order preserved)."""
    result: dict[int, list[ImageManifestEntry]] = {}
    for entry in context.image_entries:
        if entry.status is ImageStatus.DOWNLOADED and entry.filename:
            result.setdefault(entry.chapter_ordinal, []).append(entry)
    return result


def _image_abs_path(context: CrawlContext, entry: ImageManifestEntry) -> str:
    root = context.output_dir or "."
    return os.path.join(root, "img", entry.filename)


def _replace_unmatched_image_tags(html: str, allowed_filenames: set[str]) -> str:
    """Replace any remaining image-like remote/unmatched tag with ``img error``.

    Exact manifest replacement happens first.  This final pass prevents a
    missing manifest row from leaving a remote URL in EPUB/PDF output, while
    preserving only filenames that have a matching downloaded manifest entry.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(True):
        source = next((str(tag.get(attr) or "") for attr in _IMAGE_SOURCE_ATTRS if tag.get(attr)), "")
        if tag.name == "img":
            if source in allowed_filenames or source.removeprefix("img/") in allowed_filenames:
                continue
            tag.replace_with(BeautifulSoup(_IMG_ERROR_TAG, "html.parser").img)
        elif source.startswith(("http://", "https://", "//")):
            tag.replace_with(BeautifulSoup(_IMG_ERROR_TAG, "html.parser").img)
    return str(soup)


def _chapter_html(context: CrawlContext, content: dict, plan: dict[int, dict[str, list[str]]]) -> str:
    """Apply exact replacement and guarantee that image tags are local/error-only."""
    ordinal = int(content.get("ordinal", 0))
    html, _ = _apply_replacement(str(content.get("html") or ""), plan.get(ordinal, {}))
    allowed = {
        entry.filename
        for entry in _download_entry_index(context).get(ordinal, [])
        if os.path.isfile(_image_abs_path(context, entry))
    }
    return _replace_unmatched_image_tags(html, allowed)


# ---------------------------------------------------------------------------
# Safe artifact naming
# ---------------------------------------------------------------------------


def artifact_stem(context: CrawlContext, volume_title: str = "") -> str:
    """Return the sanitized base name (no extension) for an artifact."""
    if context.metadata and context.metadata.title:
        base = context.metadata.title
    else:
        try:
            base = urlsplit(context.request.url).hostname or "work"
        except Exception:  # pragma: no cover
            base = "work"
    if volume_title:
        base = f"{base} - {volume_title}"
    return _safe_filename(base, default="work")


def _unique_stem(stem: str, taken: set[str], ext: str) -> str:
    """Return *stem* with a deterministic numeric suffix on full-name collision."""
    if stem + ext not in taken:
        taken.add(stem + ext)
        return stem
    i = 1
    while f"{stem}_{i}{ext}" in taken:
        i += 1
    result = f"{stem}_{i}"
    taken.add(result + ext)
    return result


def _artifacts_dir(context: CrawlContext) -> str:
    return context.output_dir or "."


def _chapter_by_volume(context: CrawlContext) -> dict[int, list[dict]]:
    by_volume: dict[int, list[dict]] = {}
    for content in context.chapter_contents:
        by_volume.setdefault(int(content.get("volume_index", 0)), []).append(content)
    return by_volume


def _volume_title(context: CrawlContext, vol_index: int) -> str:
    for volume in context.volumes:
        if volume.index == vol_index and volume.title:
            return volume.title
    return f"Volume {vol_index + 1}"


# ---------------------------------------------------------------------------
# EPUB
# ---------------------------------------------------------------------------


def _build_epub(context: CrawlContext, chapters: list[dict], *, volume_title: str = "") -> bytes:
    """Build a single in-memory EPUB for *chapters* in source order."""
    if epub is None:
        raise ExporterError("ebooklib is required to build EPUB artifacts.")
    title = (context.metadata.title if context.metadata and context.metadata.title else "") or artifact_stem(context)
    if volume_title:
        title = f"{title} - {volume_title}"

    book = epub.EpubBook()
    book.set_identifier(re.sub(r"\W+", "", f"{title}-{len(chapters)}") or "novel")
    book.set_title(title)
    book.set_language("en")
    if context.metadata:
        if context.metadata.author:
            book.add_author(context.metadata.author)
        if context.metadata.description:
            book.add_metadata("DC", "description", context.metadata.description)
        for genre in context.metadata.genres:
            book.add_metadata("DC", "subject", genre)

    style = epub.EpubItem(
        file_name="style.css",
        media_type="text/css",
        content="body { font-family: serif; line-height: 1.6; } img { max-width: 100%; }",
    )
    book.add_item(style)

    download_index = _download_entry_index(context)
    plan = _replacement_plan(context)
    used_names: set[str] = set()
    toc: list = []
    spine: list = []

    for content in chapters:
        ordinal = int(content.get("ordinal", 0))
        title_text = str(content.get("title") or f"Chapter {ordinal}")
        html = _chapter_html(context, content, plan)

        chapter = epub.EpubHtml(
            title=title_text,
            file_name=f"ch_{ordinal:04d}.xhtml",
            lang="en",
            content=f'<div class="chapter"><h1>{escape(title_text)}</h1>{html}</div>',
        )
        epub_paths: dict[str, str] = {}
        for entry in download_index.get(ordinal, []):
            src = _image_abs_path(context, entry)
            if not os.path.exists(src):
                continue
            stem, ext = os.path.splitext(entry.filename)
            mime = _mime_for_ext(ext)
            name = f"images/{stem}{ext}"
            i = 1
            while name in used_names:
                name = f"images/{stem}_{i}{ext}"
                i += 1
            used_names.add(name)
            with open(src, "rb") as fh:
                image_data = fh.read()
            book.add_item(epub.EpubItem(file_name=name, media_type=mime, content=image_data))
            epub_paths[entry.filename] = name
        # Change only local image ``src`` attributes, never arbitrary prose or
        # URLs that merely contain an image filename.
        chapter.content = _rewrite_epub_image_refs(chapter.content, epub_paths)
        book.add_item(chapter)
        toc.append(chapter)
        spine.append(chapter)

    book.toc = toc
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    out = io.BytesIO()
    epub.write_epub(out, book, {})
    return out.getvalue()


def _rewrite_epub_image_refs(html: str | bytes, paths: dict[str, str]) -> str:
    """Rewrite exact downloaded image ``src`` values for EPUB media paths."""
    from bs4 import BeautifulSoup

    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("img"):
        src = str(tag.get("src") or "")
        filename = src.removeprefix("img/")
        if filename in paths:
            tag["src"] = paths[filename]
    return str(soup)


def _mime_for_ext(ext: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(ext.lower(), "image/jpeg")


# ---------------------------------------------------------------------------
# Readable novel PDF (fpdf2)
# ---------------------------------------------------------------------------


def _pick_fonts() -> tuple[str, str]:
    for regular, bold in _FONT_CANDIDATES:
        if os.path.exists(regular) and os.path.exists(bold):
            return regular, bold
    return _FONT_CANDIDATES[-1]


def _build_novel_pdf(context: CrawlContext, chapters: list[dict]) -> bytes:
    """Build a readable novel PDF, ignoring arbitrary site CSS and scripts."""
    if FPDF is None:
        raise ExporterError("fpdf2 is required to build PDF exports.")
    download_index = _download_entry_index(context)
    selected_entries = [
        entry for content in chapters
        for entry in download_index.get(int(content.get("ordinal", 0)), [])
    ]
    _reject_webp_for_pdf(context, selected_entries)

    regular, bold = _pick_fonts()
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(20, 20, 20)
    pdf.add_font("Novel", "", regular)
    pdf.add_font("Novel", "B", bold)
    pdf.add_page()

    plan = _replacement_plan(context)
    volume_indexes = {int(content.get("volume_index", 0)) for content in chapters}
    previous_volume: int | None = None
    for i, content in enumerate(chapters):
        if i > 0:
            pdf.add_page()
        ordinal = int(content.get("ordinal", 0))
        volume_index = int(content.get("volume_index", 0))
        if len(volume_indexes) > 1 and volume_index != previous_volume:
            pdf.set_font("Novel", "B", 15)
            pdf.multi_cell(0, 9, _strip_markup(_volume_title(context, volume_index)))
            pdf.ln(4)
        previous_volume = volume_index

        title_text = str(content.get("title") or f"Chapter {ordinal}")
        pdf.set_font("Novel", "B", 18)
        pdf.multi_cell(0, 10, _strip_markup(title_text))
        pdf.ln(4)
        html_tag_to_pdf(pdf, _chapter_html(context, content, plan), context)

    return bytes(pdf.output())


def _strip_markup(text: str) -> str:
    from bs4 import BeautifulSoup

    return BeautifulSoup(text or "", "html.parser").get_text(" ")


def _pdf_image_path(context: CrawlContext, src: str) -> str | None:
    """Resolve a Task-4 local image reference without permitting path escape."""
    source = (src or "").replace("\\", "/")
    if source.startswith("img/"):
        source = source[4:]
    if not source or "/" in source or source in {".", ".."}:
        return None
    path = os.path.join(context.output_dir or ".", "img", source)
    return path if os.path.isfile(path) else None


def _write_img_error(pdf) -> None:
    pdf.set_font("Novel", "", 12)
    pdf.set_text_color(160, 0, 0)
    pdf.multi_cell(0, 7, "img error")
    pdf.set_text_color(0, 0, 0)


def html_tag_to_pdf(pdf, html: str, context: CrawlContext) -> None:
    """Render sanitized HTML with PDF-friendly block and emphasis semantics."""
    from bs4 import BeautifulSoup, NavigableString, Tag

    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "link", "meta"]):
        tag.decompose()

    def set_style(*, strong: bool = False, emphasis: bool = False, size: int = 12) -> None:
        pdf.set_font("Novel", "B" if strong else "", size)
        pdf.set_text_color(60, 60, 60) if emphasis else pdf.set_text_color(0, 0, 0)

    def render_inline(node, *, strong: bool = False, emphasis: bool = False) -> None:
        if isinstance(node, NavigableString):
            text = re.sub(r"\s+", " ", str(node))
            if text:
                set_style(strong=strong, emphasis=emphasis)
                pdf.write(7, text)
            return
        if not isinstance(node, Tag):
            return
        name = node.name
        if name == "img":
            src = node.get("src") or ""
            local = _pdf_image_path(context, str(src))
            if local:
                try:
                    pdf.ln(2)
                    pdf.image(local, w=pdf.epw)
                    pdf.ln(2)
                except Exception:
                    _write_img_error(pdf)
            else:
                _write_img_error(pdf)
            return
        if name == "br":
            pdf.ln(3)
            return
        for child in node.children:
            render_inline(
                child,
                strong=strong or name in _STRONG_TAGS,
                emphasis=emphasis or name in _EMPH_TAGS,
            )

    def render_block(node) -> None:
        if isinstance(node, NavigableString):
            if str(node).strip():
                render_inline(node)
                pdf.ln(9)
            return
        if not isinstance(node, Tag):
            return
        name = node.name
        if name in _HEADING_TAGS:
            size = {1: 18, 2: 15, 3: 13}.get(_heading_level(node), 12)
            pdf.ln(2)
            set_style(strong=True, size=size)
            pdf.multi_cell(0, 8, _norm(node.get_text(" ")))
            pdf.ln(2)
            return
        if name == "img":
            render_inline(node)
            return
        if name in {"ul", "ol"}:
            for li in node.find_all("li", recursive=False):
                set_style()
                pdf.write(7, "• ")
                for child in li.children:
                    render_inline(child)
                pdf.ln(9)
            return
        if name in {"p", "blockquote"}:
            for child in node.children:
                render_inline(child, emphasis=name == "blockquote")
            pdf.ln(10)
            return
        if name in {"div", "section", "article", "main", "body"}:
            block_names = _HEADING_TAGS | {"p", "blockquote", "ul", "ol", "div", "section", "article"}
            if any(isinstance(child, Tag) and child.name in block_names for child in node.children):
                for child in node.children:
                    render_block(child)
            else:
                for child in node.children:
                    render_inline(child)
                pdf.ln(10)
            return
        for child in node.children:
            render_inline(child)

    for child in soup.body.children if soup.body else soup.children:
        render_block(child)
    set_style()


# Backwards-compatible internal alias for callers written before the renderer
# was made tag-aware.
_render_html_pdf = html_tag_to_pdf


def _heading_level(node) -> int:
    try:
        return int(node.name[1])
    except Exception:  # pragma: no cover
        return 1


def _norm(text: str) -> str:
    return re.sub(r"[ \t]+", " ", str(text)).replace("\u00a0", " ").strip("\n")


# ---------------------------------------------------------------------------
# Comic / gallery image PDF + CBZ
# ---------------------------------------------------------------------------


def _ordered_image_files(
    context: CrawlContext, entries: list[ImageManifestEntry] | None = None
) -> list[str]:
    """Return selected downloaded image paths in manifest source order."""
    files: list[str] = []
    seen: set[str] = set()
    for entry in entries if entries is not None else context.image_entries:
        if entry.status is not ImageStatus.DOWNLOADED or not entry.filename:
            continue
        local = _image_abs_path(context, entry)
        if os.path.exists(local) and local not in seen:
            seen.add(local)
            files.append(local)
    return files


def _reject_webp_for_pdf(context: CrawlContext, entries: list[ImageManifestEntry]) -> None:
    """Fail safely instead of producing an incomplete or unreadable PDF."""
    webp_files = [
        entry.filename for entry in entries
        if entry.status is ImageStatus.DOWNLOADED
        and entry.filename.lower().endswith(".webp")
        and os.path.isfile(_image_abs_path(context, entry))
    ]
    if webp_files:
        raise ExporterError(
            "PDF export cannot include WebP images. Use CBZ output instead "
            f"(WebP image: {webp_files[0]})."
        )


def _build_image_pdf(context: CrawlContext, entries: list[ImageManifestEntry] | None = None) -> bytes:
    """Build a PDF with each downloaded image on its own page in source order."""
    if FPDF is None:
        raise ExporterError("fpdf2 is required to build PDF exports.")
    entries = entries if entries is not None else context.image_entries
    _reject_webp_for_pdf(context, entries)
    files = _ordered_image_files(context, entries)
    if not files:
        raise ExporterError("no downloaded images to build an image PDF.")
    pdf = FPDF(format="A4", unit="pt")
    pdf.set_auto_page_break(auto=False)
    for path in files:
        pdf.add_page()
        _place_image_fit(pdf, path)
    return bytes(pdf.output())


def _place_image_fit(pdf, path: str) -> None:
    from PIL import Image

    page_w = pdf.w
    page_h = pdf.h
    with Image.open(path) as im:
        w, h = im.size
    scale = min(page_w / w, page_h / h, 1.0)
    dw, dh = w * scale, h * scale
    x = (page_w - dw) / 2
    y = (page_h - dh) / 2
    ext = os.path.splitext(path)[1].lower()
    fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".gif": "GIF", ".bmp": "BMP"}.get(ext)
    if fmt:
        try:
            pdf.image(path, x=x, y=y, w=dw, h=dh)
        except Exception as exc:
            raise ExporterError(f"failed to embed image {path}: {exc}") from exc
    else:
        raise ExporterError(f"unsupported image type for PDF: {path}")


def _build_cbz(context: CrawlContext, entries: list[ImageManifestEntry] | None = None) -> bytes:
    """Build a CBZ (ZIP) of ordered images in natural source order."""
    files = _ordered_image_files(context, entries)
    if not files:
        raise ExporterError("no downloaded images to build a CBZ archive.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for i, path in enumerate(files, 1):
            ext = os.path.splitext(path)[1] or ".jpg"
            zf.write(path, arcname=f"{i:04d}{ext.lower()}")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Folder export (retain normalized image tree + manifest, no archive)
# ---------------------------------------------------------------------------


def _build_folder(
    context: CrawlContext, dest: str, entries: list[ImageManifestEntry] | None = None
) -> None:
    """Copy only this artifact's normalized images plus its ordered manifest."""
    img_dst = os.path.join(dest, "img")
    os.makedirs(img_dst, exist_ok=True)
    selected = entries if entries is not None else context.image_entries
    for entry in selected:
        if entry.status is not ImageStatus.DOWNLOADED or not entry.filename:
            continue
        src = _image_abs_path(context, entry)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(img_dst, entry.filename))
    _write_manifest_at(context, os.path.join(img_dst, "img_info.json"), selected)


def _write_manifest_at(
    context: CrawlContext, path: str, entries: list[ImageManifestEntry] | None = None
) -> None:
    import json

    os.makedirs(os.path.dirname(path), exist_ok=True)
    selected = entries if entries is not None else context.image_entries
    with open(path, "w", encoding="utf-8") as f:
        json.dump([e.to_dict() for e in selected], f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Atomic artifact writing
# ---------------------------------------------------------------------------


def _write_bytes_atomic(dest: str, data: bytes) -> None:
    d = os.path.dirname(dest)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".export-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # ``link`` publishes an already-complete temp file atomically but
        # refuses to replace an existing artifact.  This prevents a fresh run
        # from silently overwriting a completed export.
        os.link(tmp, dest)
        os.remove(tmp)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _move_tree_atomic(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        os.rename(src, dest)
    except Exception as exc:
        raise ExporterError(f"failed to place folder artifact: {exc}") from exc


def _ext_for_format(output_format: OutputFormat) -> str:
    return {
        OutputFormat.EPUB: ".epub",
        OutputFormat.PDF: ".pdf",
        OutputFormat.CBZ: ".cbz",
        OutputFormat.FOLDER: "",
    }[output_format]


def _image_output_format(output_format: OutputFormat) -> bool:
    return output_format in (OutputFormat.CBZ, OutputFormat.PDF, OutputFormat.FOLDER)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def _validate_ready(context: CrawlContext) -> None:
    from api.contracts import is_supported_combination

    if not context.chapter_contents:
        raise IncompleteCrawlError(
            "cannot export: no chapters were crawled (chapter_contents is empty)."
        )
    if not is_supported_combination(
        context.request.content_type, context.request.output_format, context.request.packaging
    ):
        raise ExporterError(
            f"Unsupported export combination: content={context.request.content_type.value}, "
            f"format={context.request.output_format.value}, "
            f"packaging={context.request.packaging.value}."
        )


def create_ebook_handler(context: CrawlContext, params: dict) -> None:
    """Run the content-aware export for ``context.request``.

    Builds one or more artifacts (per ``packaging``), records them on
    ``context.artifacts``, and raises ``ExporterError`` on hard failure.
    """
    if not isinstance(params, dict):
        raise InvalidFlowError("create_ebook params must be a dict.")
    _validate_ready(context)

    content_type = context.request.content_type
    output_format = context.request.output_format
    packaging = context.request.packaging

    artifact_start = len(context.artifacts)
    try:
        if content_type is ContentType.NOVEL:
            if packaging is PackagingMode.COMBINED:
                _export_one(context, output_format, list(context.chapter_contents), volume_index=None)
            elif packaging is PackagingMode.PER_VOLUME:
                by_volume = _chapter_by_volume(context)
                for vol_index in sorted(by_volume):
                    _export_one(
                        context,
                        output_format,
                        by_volume[vol_index],
                        volume_index=vol_index,
                        volume_title=_volume_title(context, vol_index),
                    )
            else:
                raise ExporterError(f"novel does not support packaging={packaging.value!r}.")
        elif content_type is ContentType.COMIC:
            # A comic artifact is strictly one chapter.  Gallery is deliberately
            # handled separately because its synthetic chapter is one gallery.
            for content in sorted(context.chapter_contents, key=lambda item: int(item.get("ordinal", 0))):
                ordinal = int(content.get("ordinal", 0))
                chapter_entries = [
                    entry for entry in context.image_entries
                    if entry.chapter_ordinal == ordinal
                ]
                title = str(content.get("title") or content.get("identifier") or f"Chapter {ordinal}")
                _export_image_artifact(context, output_format, chapter_entries, artifact_title=title)
        elif content_type is ContentType.GALLERY:
            _export_image_artifact(context, output_format, list(context.image_entries))
        else:  # pragma: no cover
            raise ExporterError(f"unknown content type: {content_type.value!r}.")
    except Exception:
        _rollback_artifacts(context, artifact_start)
        raise


def _final_stem(context: CrawlContext, volume_title: str, taken: set[str], ext: str) -> str:
    stem = artifact_stem(context, volume_title)
    return _unique_stem(stem, taken, ext)


def _taken_artifact_names(context: CrawlContext) -> set[str]:
    """Names already published on disk or by this context."""
    taken = {os.path.basename(artifact.path) for artifact in context.artifacts if artifact.path}
    try:
        taken.update(os.listdir(_artifacts_dir(context)))
    except FileNotFoundError:
        pass
    return taken


def _rollback_artifacts(context: CrawlContext, start: int) -> None:
    """Remove only artifacts created by the failed current export operation."""
    for artifact in context.artifacts[start:]:
        try:
            if os.path.isdir(artifact.path):
                shutil.rmtree(artifact.path)
            elif os.path.exists(artifact.path):
                os.remove(artifact.path)
        except OSError:
            # Keep the original export exception; callers still receive a
            # failure instead of a false success even if cleanup is blocked.
            pass
    del context.artifacts[start:]


def _export_one(
    context: CrawlContext,
    output_format: OutputFormat,
    chapters: list[dict],
    *,
    volume_index: int | None,
    volume_title: str = "",
) -> ArtifactResult:
    ext = _ext_for_format(output_format)
    os.makedirs(_artifacts_dir(context), exist_ok=True)
    taken = _taken_artifact_names(context)
    unique = _final_stem(context, volume_title, taken, ext)
    final_path = os.path.join(_artifacts_dir(context), unique + ext)

    if output_format is OutputFormat.EPUB:
        data = _build_epub(context, chapters, volume_title=volume_title)
        _write_bytes_atomic(final_path, data)
    elif output_format is OutputFormat.PDF:
        data = _build_novel_pdf(context, chapters)
        _write_bytes_atomic(final_path, data)
    elif output_format is OutputFormat.FOLDER:
        tmp_dir = tempfile.mkdtemp(prefix=".folder-", dir=_artifacts_dir(context))
        try:
            _build_folder(context, tmp_dir)
            _move_tree_atomic(tmp_dir, final_path)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
    else:
        raise ExporterError(f"unexpected novel output format: {output_format.value!r}.")

    artifact = ArtifactResult(
        output_format=output_format,
        packaging=context.request.packaging,
        path=final_path,
        volume_index=volume_index,
        status="created",
    )
    context.artifacts.append(artifact)
    return artifact


def _export_image_artifact(
    context: CrawlContext,
    output_format: OutputFormat,
    entries: list[ImageManifestEntry],
    *,
    artifact_title: str = "",
) -> ArtifactResult:
    ext = _ext_for_format(output_format)
    os.makedirs(_artifacts_dir(context), exist_ok=True)
    taken = _taken_artifact_names(context)
    unique = _final_stem(context, artifact_title, taken, ext)
    final_path = os.path.join(_artifacts_dir(context), unique + ext)

    if output_format is OutputFormat.CBZ:
        data = _build_cbz(context, entries)
        _write_bytes_atomic(final_path, data)
    elif output_format is OutputFormat.PDF:
        data = _build_image_pdf(context, entries)
        _write_bytes_atomic(final_path, data)
    elif output_format is OutputFormat.FOLDER:
        tmp_dir = tempfile.mkdtemp(prefix=".folder-", dir=_artifacts_dir(context))
        try:
            _build_folder(context, tmp_dir, entries)
            _move_tree_atomic(tmp_dir, final_path)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
    else:
        raise ExporterError(f"unexpected image output format: {output_format.value!r}.")

    artifact = ArtifactResult(
        output_format=output_format,
        packaging=context.request.packaging,
        path=final_path,
        volume_index=None,
        status="created",
    )
    context.artifacts.append(artifact)
    return artifact


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_export_handlers() -> None:
    """Upgrade the canonical ``create_ebook`` module with the real handler."""
    set_module_handler("create_ebook", create_ebook_handler)


register_export_handlers()


__all__ = [
    "create_ebook_handler",
    "register_export_handlers",
    "artifact_stem",
    "resolve_image_path",
]


def resolve_image_path(context: CrawlContext, entry: ImageManifestEntry) -> str:
    """Absolute path to a downloaded image file (public helper)."""
    return _image_abs_path(context, entry)
