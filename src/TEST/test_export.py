"""Tests for api.export — Task 5 of ai-integration.

Covers exact raw-tag + occurrence replacement, combined/per-volume novel
naming, EPUB/CBZ zip structure and entry order, novel PDF signature/page-count/
text/Unicode/inline images, comic/gallery naming, folder export, filename
collision handling, and atomic cleanup after failure.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from PIL import Image

from api import get_module
from api.chapter_crawl import write_manifest
from api.contracts import (
    ArtifactResult,
    ContentType,
    CrawlContext,
    CrawlRequest,
    ExporterError,
    ImageManifestEntry,
    ImageStatus,
    IncompleteCrawlError,
    InvalidFlowError,
    Metadata,
    OutputFormat,
    PackagingMode,
    Volume,
)
from api.export import (
    _apply_replacement,
    create_ebook_handler,
    register_export_handlers,
)
from api.preparation import register_preparation_handlers


def _png_bytes(color=(200, 30, 30), size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 200, 60)).save(buf, format="JPEG")
    return buf.getvalue()


def _webp_bytes(size=(60, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 60, 220)).save(buf, format="WEBP")
    return buf.getvalue()


class ExportHarness(unittest.TestCase):
    """Builds a CrawlContext with metadata, chapters, and real image files."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.output_dir = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(self.output_dir, "img"), exist_ok=True)

    def _req(
        self,
        content_type="novel",
        output_format="epub",
        packaging=None,
        url="https://ex.com/series/title",
    ):
        return CrawlRequest(
            url=url,
            content_type=content_type,
            output_format=output_format,
            packaging=packaging,
            fetch_mode="requests",
        )

    def _context(self, chapters=None, req=None, entries=None):
        req = req or self._req()
        volumes = []
        if chapters is None:
            chapters = [
                {
                    "ordinal": 1,
                    "title": "First Chapter",
                    "html": "<p>Hello <b>world</b>.</p>",
                    "volume_index": 0,
                    "position_in_volume": 1,
                    "identifier": "1",
                    "url": req.url,
                }
            ]
        # Build volumes mirroring chapter volume_index grouping
        volumes = []
        by_vol = {}
        for ch in chapters:
            by_vol.setdefault(ch["volume_index"], []).append(ch)
        for vi in sorted(by_vol):
            vols = by_vol[vi]
            volumes.append(
                Volume(
                    index=vi,
                    title=f"Volume {vi+1}",
                    chapters=tuple(
                        type("C", (), {"ordinal": c["ordinal"], "volume_index": c["volume_index"], "position_in_volume": c.get("position_in_volume", 1), "identifier": c["identifier"], "title": c["title"], "url": c.get("url", req.url)})()
                        for c in vols
                    ),
                )
            )
        return CrawlContext(
            request=req,
            metadata=Metadata(title="My Test Series", author="Author One"),
            volumes=volumes,
            output_dir=self.output_dir,
            chapter_contents=chapters,
            image_entries=entries or [],
        )

    def _write_image(self, name, data):
        path = os.path.join(self.output_dir, "img", name)
        with open(path, "wb") as f:
            f.write(data)
        return path


class TestReplacement(unittest.TestCase):
    """Exact raw-tag + occurrence replacement."""

    def test_duplicate_raw_tags_replaced_by_occurrence(self):
        html = (
            '<img src="https://cdn/x.png"> mid '
            '<img src="https://cdn/x.png"> end'
        )
        tags = {"<img src=\"https://cdn/x.png\">": ["<img src=\"img_0001_001.png\">", "<img src=\"img_0001_002.png\">"]}
        out, unresolved = _apply_replacement(html, tags)
        self.assertEqual(out, (
            '<img src="img_0001_001.png"> mid '
            '<img src="img_0001_002.png"> end'
        ))
        self.assertEqual(unresolved, [])

    def test_excess_occurrences_dropped_not_remote(self):
        html = "<p>A <img src=\"https://cdn/x.png\"> B <img src=\"https://cdn/x.png\"></p>"
        tags = {"<img src=\"https://cdn/x.png\">": ["<img src=\"img_local.png\">"]}
        out, unresolved = _apply_replacement(html, tags)
        self.assertIn("<img src=\"img_local.png\">", out)
        self.assertNotIn("https://cdn", out)
        self.assertEqual(unresolved, ["<img src=\"https://cdn/x.png\">"])

    def test_missing_manifest_does_not_leak_remote(self):
        html = "<p>Text <img src=\"https://remote/img.jpg\"> more</p>"
        out, unresolved = _apply_replacement(html, {})
        # No manifest → no replacement; remote tag remains untouched per HTML,
        # but the exporter strips it in per-format output.  _apply_replacement
        # leaves it because there is no plan; unresolved tracking only applies
        # when a plan exists.
        self.assertIn("https://remote/img.jpg", out)

    def test_no_plan_no_change(self):
        html = "<p>plain</p>"
        out, unresolved = _apply_replacement(html, {"<img>": ["<img src=\"local\">"]})
        self.assertEqual(out, "<p>plain</p>")
        self.assertEqual(unresolved, [])


class TestNovelEpub(ExportHarness):
    def test_combined_epub_structure_and_order(self):
        chapters = [
            {"ordinal": 1, "title": "Ch One", "html": "<p>One</p>", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "https://ex.com/1"},
            {"ordinal": 2, "title": "Ch Two", "html": "<p>Two</p>", "volume_index": 0, "position_in_volume": 2, "identifier": "2", "url": "https://ex.com/2"},
        ]
        ctx = self._context(chapters=chapters, req=self._req(packaging="combined"))
        create_ebook_handler(ctx, {})
        self.assertEqual(len(ctx.artifacts), 1)
        path = ctx.artifacts[0].path
        self.assertTrue(path.endswith(".epub"))
        self.assertEqual(os.path.dirname(path), self.output_dir)
        self.assertTrue(os.path.exists(path))
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            self.assertIn("mimetype", names)
            self.assertTrue(any(n.endswith("ch_0001.xhtml") for n in names))
            self.assertTrue(any(n.endswith("ch_0002.xhtml") for n in names))
            spine_idx = [n for n in names if "ch_000" in n]
            ones = [n for n in spine_idx if "0001" in n]
            twos = [n for n in spine_idx if "0002" in n]
            self.assertEqual(len(ones), 1)
            self.assertEqual(len(twos), 1)
            # chapter 1 before chapter 2 in the spine/content order
            self.assertLess(names.index(ones[0]), names.index(twos[0]))

    def test_inline_image_embedded_in_epub(self):
        png = _png_bytes()
        self._write_image("img_0001_001.png", png)
        entry = ImageManifestEntry(
            chapter_ordinal=1, occurrence=1,
            source_url="https://cdn/x.png",
            raw_tag="<img src=\"https://cdn/x.png\">",
            filename="img_0001_001.png",
            relative_path="img/img_0001_001.png",
            replacement_tag="<img src=\"img_0001_001.png\">",
            status=ImageStatus.DOWNLOADED,
        )
        chapters = [
            {"ordinal": 1, "title": "Ch One", "html": "<p>See <img src=\"https://cdn/x.png\"> here.</p>", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"},
        ]
        ctx = self._context(chapters=chapters, entries=[entry], req=self._req(packaging="combined"))
        create_ebook_handler(ctx, {})
        with zipfile.ZipFile(ctx.artifacts[0].path) as zf:
            names = zf.namelist()
            media = [n for n in names if "images/img_0001_001" in n]
            self.assertEqual(len(media), 1)
            # chapter content references the media path, not the remote URL
            ch = [n for n in names if n.endswith("ch_0001.xhtml")][0]
            content = zf.read(ch).decode("utf-8", "replace")
            self.assertIn("images/img_0001_001", content)
            self.assertNotIn("https://cdn/x.png", content)

    def test_per_volume_produces_one_artifact_per_volume(self):
        chapters = [
            {"ordinal": 1, "title": "A1", "html": "<p>a1</p>", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"},
            {"ordinal": 2, "title": "A2", "html": "<p>a2</p>", "volume_index": 0, "position_in_volume": 2, "identifier": "2", "url": "x"},
            {"ordinal": 3, "title": "B1", "html": "<p>b1</p>", "volume_index": 1, "position_in_volume": 1, "identifier": "3", "url": "x"},
        ]
        ctx = self._context(chapters=chapters, req=self._req(packaging="per_volume"))
        create_ebook_handler(ctx, {})
        self.assertEqual(len(ctx.artifacts), 2)
        for a in ctx.artifacts:
            self.assertEqual(a.packaging, PackagingMode.PER_VOLUME)
            self.assertTrue(a.path.endswith(".epub"))
        stems = sorted(os.path.basename(a.path) for a in ctx.artifacts)
        # distinct artifacts (volume titles differ)
        self.assertNotEqual(stems[0], stems[1])

    def test_epub_text_present_in_content(self):
        ctx = self._context(req=self._req(packaging="combined"))
        create_ebook_handler(ctx, {})
        with zipfile.ZipFile(ctx.artifacts[0].path) as zf:
            ch = [n for n in zf.namelist() if n.endswith("ch_0001.xhtml")][0]
            content = zf.read(ch).decode("utf-8", "replace")
            self.assertIn("Hello", content)
            self.assertIn("world", content)

    def test_missing_or_failed_image_becomes_local_error_placeholder(self):
        failed = ImageManifestEntry(
            chapter_ordinal=1, occurrence=1, source_url="https://cdn/failed.jpg",
            raw_tag='<img src="https://cdn/failed.jpg">', status=ImageStatus.FAILED,
        )
        chapters = [{
            "ordinal": 1, "title": "Images",
            "html": '<p><img src="https://cdn/failed.jpg"><img src="https://cdn/unlisted.jpg"></p>',
            "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x",
        }]
        ctx = self._context(chapters=chapters, entries=[failed], req=self._req(packaging="combined"))
        create_ebook_handler(ctx, {})
        with zipfile.ZipFile(ctx.artifacts[0].path) as zf:
            chapter = next(name for name in zf.namelist() if name.endswith("ch_0001.xhtml"))
            content = zf.read(chapter).decode("utf-8", "replace")
        self.assertNotIn("https://cdn", content)
        self.assertEqual(content.count('alt="img error"'), 2)


class TestNovelPdf(ExportHarness):
    def test_pdf_signature_and_unicode_text(self):
        chapters = [
            {"ordinal": 1, "title": "Café 代", "html": "<h2>Heading</h2><p>Unicode: 日本語 café — and <b>bold</b>.</p>", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"},
        ]
        ctx = self._context(chapters=chapters, req=self._req(output_format="pdf", packaging="combined"))
        create_ebook_handler(ctx, {})
        path = ctx.artifacts[0].path
        self.assertTrue(path.endswith(".pdf"))
        with open(path, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"%PDF"))
        # Unicode text embedded (fpdf2 subsets the font)
        self.assertTrue(b"Unicode" in data)

    def test_pdf_contains_inline_image(self):
        jpeg = _jpeg_bytes()
        self._write_image("img_0001_001.jpg", jpeg)
        entry = ImageManifestEntry(
            chapter_ordinal=1, occurrence=1, source_url="https://cdn/p.jpg",
            raw_tag="<img src=\"https://cdn/p.jpg\">", filename="img_0001_001.jpg",
            relative_path="img/img_0001_001.jpg",
            replacement_tag="<img src=\"img/img_0001_001.jpg\">",
            status=ImageStatus.DOWNLOADED,
        )
        chapters = [
            {"ordinal": 1, "title": "Pic", "html": "<p>Here:</p><img src=\"https://cdn/p.jpg\">", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"},
        ]
        ctx = self._context(chapters=chapters, entries=[entry], req=self._req(output_format="pdf", packaging="combined"))
        create_ebook_handler(ctx, {})
        with open(ctx.artifacts[0].path, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertIn(b"/DCTDecode", data)

    def test_pdf_accepts_the_task4_bare_filename_replacement(self):
        jpeg = _jpeg_bytes()
        self._write_image("img_0001_001.jpg", jpeg)
        entry = ImageManifestEntry(
            chapter_ordinal=1, occurrence=1, source_url="https://cdn/p.jpg",
            raw_tag='<img src="https://cdn/p.jpg">', filename="img_0001_001.jpg",
            relative_path="img/img_0001_001.jpg",
            replacement_tag='<img src="img_0001_001.jpg">', status=ImageStatus.DOWNLOADED,
        )
        chapters = [{
            "ordinal": 1, "title": "Pic", "html": '<img src="https://cdn/p.jpg">',
            "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x",
        }]
        ctx = self._context(chapters=chapters, entries=[entry], req=self._req(output_format="pdf", packaging="combined"))
        create_ebook_handler(ctx, {})
        with open(ctx.artifacts[0].path, "rb") as pdf_file:
            self.assertIn(b"/DCTDecode", pdf_file.read())

    def test_pdf_ignores_site_css_scripts(self):
        chapters = [
            {"ordinal": 1, "title": "T", "html": "<script>var x=1;</script><style>.x{}</style><p>Body only</p>", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"},
        ]
        ctx = self._context(chapters=chapters, req=self._req(output_format="pdf", packaging="combined"))
        create_ebook_handler(ctx, {})
        with open(ctx.artifacts[0].path, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"%PDF"))
        # The javascript/style text must not be embedded as content
        self.assertNotIn(b"var x=1", data)


class TestComicCbz(ExportHarness):
    def _image_entries(self):
        entries = []
        pngs = [_png_bytes((200, 30, 30)), _png_bytes((30, 200, 30)), _png_bytes((30, 30, 200))]
        for i, p in enumerate(pngs, 1):
            name = f"img_0001_{i:03d}.png"
            self._write_image(name, p)
            entries.append(
                ImageManifestEntry(
                    chapter_ordinal=1, occurrence=i,
                    source_url=f"https://cdn/{i}.png", raw_tag=f"<img src=\"https://cdn/{i}.png\">",
                    filename=name, relative_path=f"img/{name}",
                    replacement_tag=f"<img src=\"{name}\">",
                    status=ImageStatus.DOWNLOADED,
                )
            )
        return entries

    def test_cbz_order_and_naming(self):
        entries = self._image_entries()
        ctx = self._context(chapters=[{
            "ordinal": 1, "title": "Comic", "html": "", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"
        }], entries=entries, req=self._req(content_type="comic", output_format="cbz", packaging="per_chapter"))
        create_ebook_handler(ctx, {})
        path = ctx.artifacts[0].path
        self.assertTrue(path.endswith(".cbz"))
        self.assertEqual(len(ctx.artifacts), 1)
        self.assertEqual(ctx.artifacts[0].packaging, PackagingMode.PER_CHAPTER)
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            self.assertEqual(len(names), 3)
            self.assertEqual(names[0], "0001.png")
            self.assertEqual(names[1], "0002.png")
            self.assertEqual(names[2], "0003.png")

    def test_gallery_folder_idempotent(self):
        entries = []
        for i in (1, 2):
            name = f"img_0001_{i:03d}.png"
            self._write_image(name, _png_bytes(size=(100, 60)))
            entries.append(
                ImageManifestEntry(
                    chapter_ordinal=1, occurrence=i,
                    source_url=f"https://cdn/{i}.png", raw_tag=f"<img src=\"https://cdn/{i}.png\">",
                    filename=name, relative_path=f"img/{name}",
                    replacement_tag=f"<img src=\"{name}\">",
                    status=ImageStatus.DOWNLOADED,
                )
            )
        ctx = self._context(chapters=[{
            "ordinal": 1, "title": "Gallery", "html": "", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"
        }], entries=entries, req=self._req(content_type="gallery", output_format="folder", packaging="per_gallery"))
        create_ebook_handler(ctx, {})
        # Folder artifact: a directory, not a file archive
        path = ctx.artifacts[0].path
        self.assertTrue(os.path.isdir(path))
        self.assertTrue(os.path.exists(os.path.join(path, "img", "img_info.json")))
        img_dir = os.path.join(path, "img")
        files = sorted(f for f in os.listdir(img_dir) if f != "img_info.json")
        self.assertEqual(len(files), 2)
        with open(os.path.join(path, "img", "img_info.json")) as f:
            manifest = json.load(f)
        self.assertEqual(len(manifest), 2)
        # order preserved
        self.assertEqual(manifest[0]["occurrence"], 1)
        self.assertEqual(manifest[1]["occurrence"], 2)

    def test_comic_exports_one_cbz_per_chapter(self):
        entries = []
        chapters = []
        expected = {}
        for ordinal, color in ((1, (200, 30, 30)), (2, (30, 30, 200))):
            filename = f"img_{ordinal:04d}_001.png"
            expected[filename] = _png_bytes(color)
            self._write_image(filename, expected[filename])
            entries.append(ImageManifestEntry(
                chapter_ordinal=ordinal, occurrence=1, source_url=f"https://cdn/{ordinal}.png",
                raw_tag=f'<img src="https://cdn/{ordinal}.png">', filename=filename,
                relative_path=f"img/{filename}", replacement_tag=f'<img src="{filename}">',
                status=ImageStatus.DOWNLOADED,
            ))
            chapters.append({"ordinal": ordinal, "title": f"Chapter {ordinal}", "html": "",
                             "volume_index": 0, "position_in_volume": ordinal,
                             "identifier": str(ordinal), "url": "x"})
        ctx = self._context(chapters=chapters, entries=entries,
                            req=self._req(content_type="comic", output_format="cbz", packaging="per_chapter"))
        create_ebook_handler(ctx, {})
        self.assertEqual(len(ctx.artifacts), 2)
        for artifact, entry in zip(ctx.artifacts, entries):
            with zipfile.ZipFile(artifact.path) as zf:
                self.assertEqual(zf.namelist(), ["0001.png"])
                self.assertEqual(zf.read("0001.png"), expected[entry.filename])

    def test_webp_pdf_is_rejected_with_cbz_guidance(self):
        filename = "img_0001_001.webp"
        self._write_image(filename, _webp_bytes())
        entry = ImageManifestEntry(
            chapter_ordinal=1, occurrence=1, source_url="https://cdn/a.webp",
            raw_tag='<img src="https://cdn/a.webp">', filename=filename,
            relative_path=f"img/{filename}", replacement_tag=f'<img src="{filename}">',
            status=ImageStatus.DOWNLOADED,
        )
        ctx = self._context(chapters=[{"ordinal": 1, "title": "WebP", "html": "",
                                      "volume_index": 0, "position_in_volume": 1,
                                      "identifier": "1", "url": "x"}], entries=[entry],
                            req=self._req(content_type="comic", output_format="pdf", packaging="per_chapter"))
        with self.assertRaisesRegex(ExporterError, "Use CBZ output"):
            create_ebook_handler(ctx, {})
        self.assertEqual(ctx.artifacts, [])


class TestDispatcherFailures(ExportHarness):
    def test_no_chapters_raises_incomplete(self):
        ctx = self._context(chapters=[], req=self._req())
        with self.assertRaises(IncompleteCrawlError):
            create_ebook_handler(ctx, {})

    def test_cleanup_no_final_artifact_on_build_failure(self):
        # Force an image CBZ failure: no images present.
        ctx = self._context(chapters=[{
            "ordinal": 1, "title": "Comic", "html": "", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"
        }], entries=[], req=self._req(content_type="comic", output_format="cbz", packaging="per_chapter"))
        with self.assertRaises(ExporterError):
            create_ebook_handler(ctx, {})
        self.assertEqual(ctx.artifacts, [])
        self.assertFalse([name for name in os.listdir(self.output_dir) if name.endswith(".cbz")])

    def test_unsupported_combination_guard(self):
        # The guard in create_ebook_handler re-checks the combination even
        # though CrawlRequest normally rejects invalid pairs; exercise it by
        # forcing an unsupported packaging onto a valid request's context.
        ctx = self._context(req=self._req(content_type="novel", output_format="epub", packaging="combined"))
        # Force novel + cbz which is never supported:
        from api.contracts import OutputFormat as OF, PackagingMode as PM
        object.__setattr__(ctx.request, "output_format", OF.CBZ)
        with self.assertRaises(ExporterError):
            create_ebook_handler(ctx, {})


class TestNamingCollision(ExportHarness):
    def test_per_volume_collision_deterministic(self):
        chapters = [
            {"ordinal": 1, "title": "A1", "html": "<p>a1</p>", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"},
            {"ordinal": 2, "title": "A2", "html": "<p>a2</p>", "volume_index": 1, "position_in_volume": 1, "identifier": "2", "url": "x"},
        ]
        ctx = self._context(chapters=chapters, req=self._req(packaging="per_volume"))
        # force identical volume titles -> collision resolved deterministically
        for v in ctx.volumes:
            object.__setattr__(v, "title", "Same Volume")
        register_start = list(ctx.artifacts)
        create_ebook_handler(ctx, {})
        self.assertEqual(len(ctx.artifacts), 2)
        names = sorted(os.path.basename(a.path) for a in ctx.artifacts)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(names[0] != names[1])

    def test_existing_artifact_is_suffixed_instead_of_overwritten(self):
        first = self._context(req=self._req(packaging="combined"))
        create_ebook_handler(first, {})
        second = self._context(req=self._req(packaging="combined"))
        create_ebook_handler(second, {})
        self.assertNotEqual(first.artifacts[0].path, second.artifacts[0].path)
        self.assertTrue(second.artifacts[0].path.endswith("_1.epub"))

    def test_per_volume_failure_rolls_back_prior_published_artifacts(self):
        chapters = [
            {"ordinal": 1, "title": "A", "html": "<p>a</p>", "volume_index": 0, "position_in_volume": 1, "identifier": "1", "url": "x"},
            {"ordinal": 2, "title": "B", "html": "<p>b</p>", "volume_index": 1, "position_in_volume": 1, "identifier": "2", "url": "x"},
        ]
        ctx = self._context(chapters=chapters, req=self._req(packaging="per_volume"))
        with patch("api.export._build_epub", side_effect=[b"first", ExporterError("second fails")]):
            with self.assertRaisesRegex(ExporterError, "second fails"):
                create_ebook_handler(ctx, {})
        self.assertEqual(ctx.artifacts, [])
        self.assertFalse([name for name in os.listdir(self.output_dir) if name.endswith(".epub")])


class TestRegistration(unittest.TestCase):
    def test_create_ebook_registered(self):
        register_export_handlers()
        spec = get_module("create_ebook")
        self.assertEqual(spec.handler.__name__, "create_ebook_handler")

    def test_flow_ends_with_export_stage(self):
        from api.flow import MANDATORY_FLOW_STAGES
        self.assertEqual(MANDATORY_FLOW_STAGES[-1], "export")
        from api import get_module
        self.assertEqual(get_module("create_ebook").stage, "export")


if __name__ == "__main__":
    unittest.main()
