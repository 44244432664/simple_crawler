"""Regression tests for embedding downloaded chapter images in EPUB files."""

import io
import os
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from PIL import Image

from crawler.Novel import NovelCrawler
from utils.novel import make_book_epub, make_chapter_epub, make_volume_epub


class EpubImagePackagingTests(unittest.TestCase):
    def _write_png(self, path):
        Image.new("RGBA", (8, 8), (20, 40, 60, 128)).save(path, "PNG")

    def test_chapter_image_uses_a_portable_epub_path_and_jpeg_bytes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, "chapter-11.jpg")
            self._write_png(source_path)  # Simulates a PNG/WebP response saved as .jpg by an older crawl.

            chapter, images = make_chapter_epub(
                {
                    "chapter_title": "Chapter 11",
                    "chapter_content": f'<p><img src="{source_path}"/></p>',
                    "chapter_img_folder": tmpdir,
                },
                title_added=("Vol 1", ""),
                idx=11,
                update_log=lambda _message: None,
            )

            self.assertEqual(len(images), 1)
            self.assertTrue(images[0].file_name.startswith("images/"))
            self.assertNotIn(tmpdir, images[0].file_name)
            self.assertEqual(images[0].media_type, "image/jpeg")
            self.assertIn(images[0].file_name, chapter.content)
            with Image.open(io.BytesIO(images[0].content)) as packaged_image:
                self.assertEqual(packaged_image.format, "JPEG")

    def test_relative_foxaholic_image_source_is_replaced_after_download(self):
        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler.keep_logged_in = False
        crawler.driver = None
        crawler.fetcher = None
        crawler.base_url = "https://www.foxaholic.com"
        crawler.update_log = lambda _message: None
        crawler.format_data = {
            "img_referrer": True,
            "chapter": {
                "title": {"name": "h1"},
                "content": {"name": "div", "class_": "reading-content"},
                "remove": [],
                "image": {
                    "name": "img",
                    "other_attr": "src",
                    "allowed_hosts": ["www.foxaholic.com"],
                    "delete": 0,
                },
            },
        }
        crawler._get_page_content = lambda *_args, **_kwargs: """
            <html><h1>Chapter 11</h1>
            <div class='reading-content'><img src='/wp-content/uploads/chapter-11.png'></div></html>
        """

        with patch(
            "crawler.Novel.download_image", return_value="/tmp/chapter-11_img1.jpg"
        ) as download:
            chapter = crawler.crawl_chapter_(
                "https://www.foxaholic.com/novel/example/chapter-11/",
                img_output_dir="/tmp",
                img_prefix="chapter-11",
            )

        self.assertEqual(
            download.call_args.args[0],
            "https://www.foxaholic.com/wp-content/uploads/chapter-11.png",
        )
        self.assertIn('src="/tmp/chapter-11_img1.jpg"', chapter["chapter_content"])

    def test_book_converts_a_mislabelled_cover_and_includes_chapter_image(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cover_path = os.path.join(tmpdir, "cover.jpg")
            image_path = os.path.join(tmpdir, "chapter-11.jpg")
            self._write_png(cover_path)
            self._write_png(image_path)
            chapter, _images = make_chapter_epub(
                {
                    "chapter_title": "Chapter 11",
                    "chapter_content": f'<img src="{image_path}"/>',
                    "chapter_img_folder": tmpdir,
                },
                title_added=("Vol 1", ""),
                idx=11,
                update_log=lambda _message: None,
            )
            volume = make_volume_epub(
                {"title": "Volume 1", "cover_image": None}, [(chapter, _images)]
            )
            epub_path = make_book_epub(
                {
                    "title": "EPUB image test",
                    "author": "Tester",
                    "other_info": {},
                    "cover_image": cover_path,
                    "description": "Test",
                    "genres": [],
                    "novel_url": "https://example.test/",
                },
                [volume],
                ("", ""),
                tmpdir,
                "test.epub",
            )

            with zipfile.ZipFile(epub_path) as archive:
                names = archive.namelist()
                self.assertIn("EPUB/cover.jpg", names)
                self.assertTrue(any(name.startswith("EPUB/images/") for name in names))
                with Image.open(io.BytesIO(archive.read("EPUB/cover.jpg"))) as cover:
                    self.assertEqual(cover.format, "JPEG")


if __name__ == "__main__":
    unittest.main()
