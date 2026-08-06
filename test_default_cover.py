import importlib.util
import json
import os
import tempfile
import unittest
import zipfile

from PIL import Image

from utils.novel import generate_default_cover


class DefaultCoverGenerationTests(unittest.TestCase):
    def test_generates_non_empty_png(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cover_path = generate_default_cover(
                "A Practical Guide to Another World",
                "Nguyen Hai Dang",
                output_dir=tmpdir,
                background_dir=os.path.join(tmpdir, "missing"),
            )

            self.assertTrue(os.path.exists(cover_path))
            self.assertGreater(os.path.getsize(cover_path), 0)
            with Image.open(cover_path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (1200, 1800))

    def test_missing_background_folder_uses_procedural_background(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cover_path = generate_default_cover(
                "No Background Folder",
                "Unknown",
                output_dir=tmpdir,
                background_dir=os.path.join(tmpdir, "does-not-exist"),
            )

            self.assertTrue(os.path.exists(cover_path))

    def test_uses_supported_background_formats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            background_dir = os.path.join(tmpdir, "backgrounds")
            os.makedirs(background_dir)
            for ext, color in (("png", "navy"), ("jpg", "darkgreen"), ("webp", "maroon")):
                Image.new("RGB", (320, 480), color).save(os.path.join(background_dir, f"bg.{ext}"))

            cover_path = generate_default_cover(
                "Background Format Test",
                "Cover Bot",
                output_dir=os.path.join(tmpdir, "out"),
                background_dir=background_dir,
            )

            self.assertTrue(os.path.exists(cover_path))
            with Image.open(cover_path) as image:
                self.assertEqual(image.format, "PNG")

    def test_long_unicode_text_does_not_crash(self):
        title = "Hành trình của người giữ sách và những bí mật dài đằng đẵng trong thành phố mưa"
        author = "Tác giả có tên rất dài Nguyễn Văn A và Trần Thị B"
        with tempfile.TemporaryDirectory() as tmpdir:
            cover_path = generate_default_cover(title, author, output_dir=tmpdir)

            self.assertTrue(os.path.exists(cover_path))


@unittest.skipIf(importlib.util.find_spec("ebooklib") is None, "ebooklib is not installed")
class DefaultCoverEpubIntegrationTests(unittest.TestCase):
    def test_make_epub_generates_cover_for_missing_cover_info(self):
        from crawler.Novel import NovelCrawler

        with tempfile.TemporaryDirectory() as tmpdir:
            novel_info_path = os.path.join(tmpdir, "novel_info.json")
            data = {
                "info": {
                    "title": "Missing Cover EPUB",
                    "author": "EPUB Tester",
                    "other_info": {},
                    "cover_image": None,
                    "description": "A short description.",
                    "genres": ["Test"],
                    "novel_url": "https://example.test/novel",
                },
                "volumes": [
                    {
                        "title": "Volume 1",
                        "cover_image": None,
                        "chapter_contents": [
                            {
                                "chapter_title": "Chapter 1",
                                "chapter_content": "<p>Hello world.</p>",
                                "images": [],
                            }
                        ],
                    }
                ],
            }
            with open(novel_info_path, "w", encoding="utf-8") as file:
                json.dump(data, file)

            crawler = NovelCrawler(url="", output_dir=None)
            crawler.output_dir = tmpdir
            crawler.make_epub(novel_info_path=tmpdir)

            epub_path = os.path.join(tmpdir, "Missing Cover EPUB.epub")
            cover_path = os.path.join(tmpdir, "img", "default_cover.png")
            self.assertTrue(os.path.exists(epub_path))
            self.assertTrue(os.path.exists(cover_path))
            with zipfile.ZipFile(epub_path) as archive:
                names = archive.namelist()
            self.assertTrue(any(name.endswith("default_cover.png") for name in names))
            self.assertTrue(any(name.endswith("cover.xhtml") for name in names))
            self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()
