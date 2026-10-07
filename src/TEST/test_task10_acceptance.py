"""Task 10 acceptance tests for the flow-driven release.

All crawls use temporary aliases/formats and mocked page/image services.  No
live site, browser, AI provider, or project data file is modified.
"""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from api import (
    CrawlRequest,
    FormatResolver,
    InvalidRequestError,
    MissingFlowError,
    register_export_handlers,
    register_preparation_handlers,
    register_chapter_crawl_handlers,
    run_crawl,
)
from api.ai_update import register_site_definition, save_draft, validate_site_definition
from api.contracts import AIValidationError, IncompleteCrawlError
from crawler.Comic import run as legacy_comic_run
from crawler.Gallery import run as legacy_gallery_run
from crawler.Novel import run as legacy_novel_run
from utils.crawl_multi import crawl_multi


FLOW = [
    {"module": "get_metadata", "params": {"expected_selector": "h1.title"}},
    {"module": "volumes_prepare", "params": {"expected_selector": "div.chapters"}},
    {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
    {"module": "create_ebook"},
]


def _image_bytes(image_format: str = "PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (24, 16), (40, 120, 210)).save(buffer, format=image_format)
    return buffer.getvalue()


def _format(content_type: str, actions: list[str], *, gallery: bool = False) -> dict:
    definition = {
        "content_type": content_type,
        "title": {"name": "h1", "class_": "title"},
        "chapter_list": {
            "container": {"name": "div", "class_": "chapters"},
            "link": {"name": "a", "other_attr": "href"},
        },
        "chapter": {
            "title": {"name": "h2", "class_": "chapter-title"},
            "content": {"name": "div", "class_": "chapter-body"},
            "image": {"name": "img", "other_attr": "src"},
        },
        "fetch": {"mode": "requests", "cloudflare": False},
        "flow": [
            FLOW[0],
            FLOW[1],
            {"module": "crawl_chapter", "params": {"actions": actions}},
            FLOW[3],
        ],
    }
    if gallery:
        definition.pop("chapter_list")
        definition["gallery_links"] = {
            "container": {"name": "div", "class_": "gallery"},
            "link": {"name": "a", "other_attr": "href"},
        }
    return definition


def _volume_format() -> dict:
    """A real two-volume fixture for per-volume novel acceptance coverage."""
    definition = _format("novel", ["crawl_text_content"])
    definition.pop("chapter_list")
    definition["vol_group"] = {
        "vol_section": {"name": "section", "class_": "volume"},
        "vol_title": {"name": "h2", "class_": "volume-title"},
        "chapter_list": {"name": "div", "class_": "chapters"},
    }
    return definition


def _ai_valid_definition() -> dict:
    return {
        "content_type": "novel",
        "title": {"name": "h1", "class_": "title"},
        "chapter_list": {
            "container": {"name": "div", "class_": "chapters"},
            "link": {"name": "a", "other_attr": "href"},
        },
        "chapter_list_order": "oldest_first",
        "chapter_naming": {"source": "text", "regex": r"(\d+(?:\.\d+)?)", "capture": 1},
        "chapter": {
            "title": {"name": "h2", "class_": "chapter-title"},
            "content": {"name": "div", "class_": "chapter-body"},
        },
        "flow": [
            {"module": "get_metadata"},
            {"module": "volumes_prepare"},
            {"module": "crawl_chapter", "params": {"actions": ["crawl_text_content"]}},
            {"module": "create_ebook"},
        ],
    }


class FixturePipelineTests(unittest.TestCase):
    """Run real preparation, crawl, manifest, and export handlers offline."""

    def setUp(self) -> None:
        # Earlier unit tests replace global handlers; acceptance fixtures must
        # always exercise the production pipeline implementations.
        register_preparation_handlers()
        register_chapter_crawl_handlers()
        register_export_handlers()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.formats = self.root / "formats"
        self.formats.mkdir()
        self.aliases = self.root / "aliases.csv"
        self._write_aliases([])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_aliases(self, rows: list[tuple[str, str]]) -> None:
        with self.aliases.open("w", encoding="utf-8") as handle:
            handle.write("site,name,crawler_class\n")
            for site, name in rows:
                handle.write(f"{site},{name},FixtureCrawler\n")

    def _add_site(self, host: str, name: str, definition: dict) -> None:
        with self.aliases.open("a", encoding="utf-8") as handle:
            handle.write(f"{host},{name},FixtureCrawler\n")
        (self.formats / f"{name}.json").write_text(
            json.dumps(definition, ensure_ascii=False), encoding="utf-8"
        )

    def _run(
        self,
        request: CrawlRequest,
        pages: dict[str, str],
        *,
        image_status: int = 200,
    ):
        def get_page(url: str, expected_selector: str | None = None) -> str:
            try:
                return pages[url]
            except KeyError as exc:
                raise AssertionError(f"unexpected fixture page request: {url}") from exc

        response = MagicMock(
            status_code=image_status,
            headers={"Content-Type": "image/png"},
            content=_image_bytes(),
        )
        with patch("api.raw_page.RawPageService.get_raw_page", side_effect=get_page), patch(
            "api.chapter_crawl.requests.get", return_value=response
        ) as image_get:
            result = run_crawl(
                request,
                formats_dir=self.formats,
                aliases_path=self.aliases,
            )
        return result, image_get

    def test_novel_text_fixture_produces_epub_without_image_requests(self):
        host = "novel-fixture.example"
        base = f"https://{host}/book"
        self._add_site(host, "novel_text", _format("novel", ["crawl_text_content"]))
        pages = {
            base: '<h1 class="title">Fixture Novel</h1><div class="chapters"><a href="/book/1">1.1</a></div>',
            f"{base}/1": '<h2 class="chapter-title">Chapter 1.1</h2><div class="chapter-body"><p>Text only.</p></div>',
        }
        request = CrawlRequest(
            url=base,
            content_type="novel",
            output_format="epub",
            output_dir=str(self.root / "novel-text"),
            fetch_mode="requests",
        )

        result, image_get = self._run(request, pages)

        self.assertTrue(result.success)
        self.assertEqual(result.chapter_count, 1)
        self.assertEqual(result.image_count, 0)
        image_get.assert_not_called()
        artifact = Path(result.artifacts[0].path)
        self.assertTrue(artifact.is_file())
        with zipfile.ZipFile(artifact) as archive:
            chapter = next(name for name in archive.namelist() if name.endswith("ch_0001.xhtml"))
            self.assertIn("Text only.", archive.read(chapter).decode("utf-8"))

    def test_mixed_novel_fixture_produces_pdf_and_manifest(self):
        host = "novel-mixed-fixture.example"
        base = f"https://{host}/book"
        self._add_site(
            host,
            "novel_mixed",
            _format("novel", ["crawl_text_content", "download_image"]),
        )
        pages = {
            base: '<h1 class="title">Mixed Novel</h1><div class="chapters"><a href="/book/1">1</a></div>',
            f"{base}/1": '<h2 class="chapter-title">Chapter 1</h2><div class="chapter-body"><p>Mixed text.</p><img src="https://cdn.fixture/image.png"></div>',
        }
        request = CrawlRequest(
            url=base,
            content_type="novel",
            output_format="pdf",
            output_dir=str(self.root / "novel-mixed"),
            fetch_mode="requests",
        )

        result, image_get = self._run(request, pages)

        self.assertTrue(result.success)
        self.assertEqual(result.image_count, 1)
        self.assertEqual(image_get.call_count, 1)
        self.assertTrue(Path(result.artifacts[0].path).read_bytes().startswith(b"%PDF"))
        manifest = self.root / "novel-mixed" / "img" / "img_info.json"
        self.assertTrue(manifest.is_file())
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))[0]["occurrence"], 1)

    def test_comic_fixture_produces_ordered_cbz_per_chapter(self):
        host = "comic-fixture.example"
        base = f"https://{host}/comic"
        self._add_site(host, "comic_fixture", _format("comic", ["download_image"]))
        pages = {
            base: '<h1 class="title">Fixture Comic</h1><div class="chapters"><a href="/comic/1">1</a><a href="/comic/2">2</a></div>',
            f"{base}/1": '<h2 class="chapter-title">Chapter 1</h2><div class="chapter-body"><img src="https://cdn.fixture/one.png"></div>',
            f"{base}/2": '<h2 class="chapter-title">Chapter 2</h2><div class="chapter-body"><img src="https://cdn.fixture/two.png"></div>',
        }
        request = CrawlRequest(
            url=base,
            content_type="comic",
            output_format="cbz",
            output_dir=str(self.root / "comic"),
            fetch_mode="requests",
        )

        result, _ = self._run(request, pages)

        self.assertTrue(result.success)
        self.assertEqual(len(result.artifacts), 2)
        for artifact in result.artifacts:
            with zipfile.ZipFile(artifact.path) as archive:
                self.assertEqual(archive.namelist(), ["0001.png"])

    def test_gallery_fixture_produces_folder_with_ordered_manifest(self):
        host = "gallery-fixture.example"
        base = f"https://{host}/gallery"
        self._add_site(
            host,
            "gallery_fixture",
            _format("gallery", ["download_image"], gallery=True),
        )
        pages = {
            base: '<h1 class="title">Fixture Gallery</h1><div class="gallery"><a href="/gallery/1">one</a><a href="/gallery/2">two</a></div>',
            f"{base}/1": '<h2 class="chapter-title">One</h2><div class="chapter-body"><img src="https://cdn.fixture/one.png"></div>',
            f"{base}/2": '<h2 class="chapter-title">Two</h2><div class="chapter-body"><img src="https://cdn.fixture/two.png"></div>',
        }
        request = CrawlRequest(
            url=base,
            content_type="gallery",
            output_format="folder",
            output_dir=str(self.root / "gallery"),
            fetch_mode="requests",
        )

        result, _ = self._run(request, pages)

        self.assertTrue(result.success)
        artifact = Path(result.artifacts[0].path)
        self.assertTrue(artifact.is_dir())
        manifest = json.loads((artifact / "img" / "img_info.json").read_text(encoding="utf-8"))
        self.assertEqual([entry["occurrence"] for entry in manifest], [1, 1])
        self.assertEqual([entry["chapter_ordinal"] for entry in manifest], [1, 2])

    def test_failed_fixture_image_is_incomplete_without_artifact(self):
        host = "failed-fixture.example"
        base = f"https://{host}/comic"
        self._add_site(host, "failed_fixture", _format("comic", ["download_image"]))
        pages = {
            base: '<h1 class="title">Failed Comic</h1><div class="chapters"><a href="/comic/1">1</a></div>',
            f"{base}/1": '<h2 class="chapter-title">Chapter 1</h2><div class="chapter-body"><img src="https://cdn.fixture/missing.png"></div>',
        }
        output_dir = self.root / "failed"
        request = CrawlRequest(
            url=base,
            content_type="comic",
            output_format="cbz",
            output_dir=str(output_dir),
            fetch_mode="requests",
        )

        result, _ = self._run(request, pages, image_status=404)

        self.assertFalse(result.success)
        self.assertEqual(result.failures[0].code, IncompleteCrawlError.__name__)
        self.assertEqual(result.artifacts, ())
        self.assertFalse((output_dir / "artifacts").exists())

    def test_novel_per_volume_epub_and_pdf_produce_one_artifact_per_volume(self):
        host = "novel-volume-fixture.example"
        base = f"https://{host}/book"
        self._add_site(host, "novel_volume", _volume_format())
        pages = {
            base: (
                '<h1 class="title">Volume Novel</h1>'
                '<section class="volume"><h2 class="volume-title">One</h2><div class="chapters"><a href="/book/1">1</a></div></section>'
                '<section class="volume"><h2 class="volume-title">Two</h2><div class="chapters"><a href="/book/2">2</a></div></section>'
            ),
            f"{base}/1": '<h2 class="chapter-title">One</h2><div class="chapter-body"><p>One.</p></div>',
            f"{base}/2": '<h2 class="chapter-title">Two</h2><div class="chapter-body"><p>Two.</p></div>',
        }
        for output_format, suffix in (("epub", ".epub"), ("pdf", ".pdf")):
            with self.subTest(output_format=output_format):
                request = CrawlRequest(
                    url=base,
                    content_type="novel",
                    output_format=output_format,
                    packaging="per_volume",
                    output_dir=str(self.root / f"novel-volume-{output_format}"),
                    fetch_mode="requests",
                )
                result, _ = self._run(request, pages)
                self.assertTrue(result.success)
                self.assertEqual(len(result.artifacts), 2)
                for artifact in result.artifacts:
                    path = Path(artifact.path)
                    self.assertTrue(path.is_file())
                    self.assertEqual(path.suffix, suffix)
                    if output_format == "pdf":
                        self.assertTrue(path.read_bytes().startswith(b"%PDF"))
                    else:
                        with zipfile.ZipFile(path) as archive:
                            self.assertTrue(any(name.endswith(".xhtml") for name in archive.namelist()))

    def test_remaining_comic_and_gallery_output_formats_are_openable(self):
        cases = (
            ("comic", "pdf", False),
            ("comic", "folder", False),
            ("gallery", "cbz", True),
            ("gallery", "pdf", True),
        )
        for content_type, output_format, gallery in cases:
            with self.subTest(content_type=content_type, output_format=output_format):
                host = f"{content_type}-{output_format}-fixture.example"
                base = f"https://{host}/work"
                self._add_site(
                    host,
                    f"{content_type}_{output_format}",
                    _format(content_type, ["download_image"], gallery=gallery),
                )
                links_class = "gallery" if gallery else "chapters"
                pages = {
                    base: f'<h1 class="title">Fixture</h1><div class="{links_class}"><a href="/work/1">1</a></div>',
                    f"{base}/1": '<h2 class="chapter-title">One</h2><div class="chapter-body"><img src="https://cdn.fixture/one.png"></div>',
                }
                request = CrawlRequest(
                    url=base,
                    content_type=content_type,
                    output_format=output_format,
                    output_dir=str(self.root / f"{content_type}-{output_format}"),
                    fetch_mode="requests",
                )
                result, _ = self._run(request, pages)
                self.assertTrue(result.success)
                artifact = Path(result.artifacts[0].path)
                if output_format == "folder":
                    self.assertTrue((artifact / "img" / "img_info.json").is_file())
                elif output_format == "pdf":
                    self.assertTrue(artifact.read_bytes().startswith(b"%PDF"))
                else:
                    with zipfile.ZipFile(artifact) as archive:
                        self.assertEqual(archive.namelist(), ["0001.png"])


class CutoverAndChecksumTests(unittest.TestCase):
    """Protect the breaking cutover and immutable project JSON inputs."""

    def test_current_aliases_resolve_only_to_flowless_legacy_formats(self):
        resolver = FormatResolver()
        self.assertTrue(resolver.aliases)
        for host, alias in resolver.aliases.items():
            request = CrawlRequest(
                url=f"https://{host}/fixture",
                content_type="novel",
                output_format="epub",
            )
            with self.subTest(host=host):
                with self.assertRaises(MissingFlowError):
                    resolver.format_for(request)

    def test_legacy_interfaces_raise_migration_errors(self):
        with self.assertRaises(MissingFlowError):
            legacy_novel_run(novel_url="https://example.com/legacy")
        with self.assertRaises(MissingFlowError):
            legacy_comic_run("https://example.com/legacy", "/tmp/unused")
        with self.assertRaises(MissingFlowError):
            legacy_gallery_run("https://example.com/legacy")
        with self.assertRaisesRegex(InvalidRequestError, "version"):
            crawl_multi("missing-legacy-batch.json")

    def test_pre_existing_format_and_template_json_checksums(self):
        expected = {
            "data/formats/docln copy.json": "f7ff0e1fe65ed34e777c03a70a96251c9895bb3e961b8870b65e5f4a9367303d",
            "data/formats/docln.json": "c2bb53cc10bfa6b7a530ce9bf3f89d0be67c12a65a3085150f84b6b75ba929ad",
            "data/formats/ehentai.json": "f6c468c6f8d1f2e47fcb62cc63bce0439aad777bb745b81b44254ca1efda9e3e",
            "data/formats/foxaholic.json": "9b9edd6704873f21b8056d7c69246b9091ad128d31c4016b58cea52c5394cfd2",
            "data/formats/truyenqq.json": "d8827d4db831cbccbfb339b26762d6e3bb446c7f72e552cae00ee887dfd5aa5f",
            "data/formats/valvrareteam.json": "c98de59c801ac7a03550d51cd989a0f52e6b4cc622712cc21fa6c803130f3c8b",
            "data/formats/x_doctruyen14.json": "7bc9c6d3817a57a69add64604a09738d5232242327b715ae44f4113e8ba078eb",
            "data/formats/x_itruyenhay.json": "2d79d032301da942d055ae61bf46235ddcc6f787a22067afb9ccdc476d3a315e",
            "data/formats/x_truyenfull.json": "d0cd5d766b8e1c684869ab38e59718a08c41a8560163f0ade6eacaf6cd3744ce",
            "data/template/gallery.json": "82702787c8051e3db4730cf20496d5ca60065e74860d31e641bef1ef49bf7a3d",
            "data/template/site.json": "453755bb170ae50676a88bc92cfc310af24b198b40816dbc2d0bca823313018a",
            "data/template/to_crawl.json": "b153b4317a1355e1e92651131007af5d44419244100eec61a72292d3f22f576d",
        }
        root = Path(__file__).resolve().parents[1]
        actual = {
            str(path.relative_to(root))
            for path in [
                *sorted((root / "data/formats").glob("*.json")),
                *sorted((root / "data/template").glob("*.json")),
            ]
        }
        self.assertEqual(set(expected), actual)
        for relative, digest in expected.items():
            with self.subTest(path=relative):
                value = hashlib.sha256((root / relative).read_bytes()).hexdigest()
                self.assertEqual(value, digest)

    def test_ai_registration_and_persisted_drafts_are_validation_and_secret_safe(self):
        main_html = '<h1 class="title">Safe</h1><div class="chapters"><a href="/chapter/1">1.1</a></div>'
        chapter_html = '<h2 class="chapter-title">One</h2><div class="chapter-body">Text</div>'
        secret = "TASK10_NOT_A_REAL_SECRET"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            formats = root / "formats"
            aliases = root / "aliases.csv"
            with self.assertRaisesRegex(AIValidationError, "returned by validate"):
                register_site_definition(
                    {"content_type": "novel", "api_key": secret},
                    host="safe.example",
                    formats_dir=formats,
                    aliases_path=aliases,
                    confirmed=True,
                )
            self.assertFalse(formats.exists())

            validated = validate_site_definition(
                _ai_valid_definition(),
                content_type="novel",
                main_html=main_html,
                chapter_html=chapter_html,
                main_url="https://safe.example/work",
                chapter_url="https://safe.example/chapter/1",
            )
            with self.assertRaisesRegex(AIValidationError, "explicit confirmation"):
                register_site_definition(
                    validated,
                    host="safe.example",
                    formats_dir=formats,
                    aliases_path=aliases,
                )
            registration = register_site_definition(
                validated,
                host="safe.example",
                formats_dir=formats,
                aliases_path=aliases,
                confirmed=True,
            )
            candidate, report = save_draft(
                "safe.example",
                {"page_html": f"<article>{secret}</article>", "token": secret},
                [f"token={secret}"],
                drafts_dir=root / "drafts",
            )
            for path in (Path(registration.format_path), Path(candidate), Path(report)):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn(secret, content)
                self.assertNotIn("<article>", content)


if __name__ == "__main__":
    unittest.main()
