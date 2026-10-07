import unittest
import importlib.util
from unittest.mock import patch

from crawler.Novel import NovelCrawler
from crawler.X import XCrawler
from utils.novel import get_chapter_content, get_chapter_title, get_description, get_genres, get_image_urls


class XCrawlerChapterUrlTests(unittest.TestCase):
    def make_crawler(self, chapter_format):
        crawler = XCrawler.__new__(XCrawler)
        crawler.url = "https://novel.example/book"
        crawler.format_data = {"chapter-format": chapter_format}
        crawler.novel_info = {}
        crawler.start_chapter = None
        crawler.end_chapter = None
        return crawler

    def test_digit_format_generates_numbered_chapter_urls(self):
        crawler = self.make_crawler(r"\d+")

        self.assertEqual(crawler.chapter_url(2), "https://novel.example/book/2/")

    def test_prefix_format_generates_prefixed_chapter_urls(self):
        crawler = self.make_crawler(r"chapter-\d+")

        self.assertEqual(crawler.chapter_url(7), "https://novel.example/book/chapter-7/")

    def test_chapter_urls_are_inclusive(self):
        crawler = self.make_crawler(r"\d+")

        self.assertEqual(
            crawler.chapter_urls(2, 4),
            [
                "https://novel.example/book/2/",
                "https://novel.example/book/3/",
                "https://novel.example/book/4/",
            ],
        )

    def test_named_placeholder_format_is_supported(self):
        crawler = self.make_crawler("chap-{chapter}")

        self.assertEqual(crawler.chapter_url(3), "https://novel.example/book/chap-3/")

    def test_misspelled_chater_format_is_supported(self):
        crawler = self.make_crawler(r"\d+")
        crawler.format_data = {"chater-format": r"chapter-\d+"}

        self.assertEqual(crawler.chapter_url(3), "https://novel.example/book/chapter-3/")

    def test_pasted_chapter_url_is_used_as_story_base(self):
        crawler = self.make_crawler(r"\d+")
        crawler.url = "https://novel.example/book/1/"
        crawler.chapter_base_url = crawler._url_without_chapter_postfix(crawler.url)

        self.assertEqual(crawler.chapter_url(2), "https://novel.example/book/2/")

    def test_numeric_story_slug_is_not_stripped(self):
        crawler = self.make_crawler(r"\d+")
        crawler.url = "https://novel.example/story1/1/"
        crawler.chapter_base_url = crawler._url_without_chapter_postfix(crawler.url)

        self.assertEqual(crawler.chapter_base_url, "https://novel.example/story1")
        self.assertEqual(crawler.chapter_url(2), "https://novel.example/story1/2/")

    def test_url_without_scheme_is_normalized(self):
        self.assertEqual(
            XCrawler._normalize_url("novel.example/book"),
            "https://novel.example/book",
        )

    def test_site_name_format_uses_x_prefix(self):
        with patch("crawler.X.os.path.exists", return_value=True):
            self.assertEqual(
                XCrawler._format_path_for_site_name("mynovel"),
                "data/formats/x_mynovel.json",
            )

    def test_site_name_format_falls_back_to_generic_x_template(self):
        def exists(path):
            return path == "data/formats/x.json"

        with patch("crawler.X.os.path.exists", side_effect=exists):
            self.assertEqual(
                XCrawler._format_path_for_site_name("mynovel"),
                "data/formats/x.json",
            )

    def test_generated_volume_is_vol_0_without_volume_section(self):
        crawler = self.make_crawler(r"\d+")
        crawler.format_data["vol_group"] = {
            "vol_section": {
                "id": "",
                "name": "",
                "class_": "",
                "attrs": "",
            }
        }

        volume = crawler._build_generated_volume(1, 2)

        self.assertEqual(volume["title"], "vol_0")
        self.assertEqual(volume["chapter_links"], [
            "https://novel.example/book/1/",
            "https://novel.example/book/2/",
        ])

    def test_generated_volume_uses_range_title_with_volume_section(self):
        crawler = self.make_crawler(r"\d+")
        crawler.format_data["vol_group"] = {
            "vol_section": {
                "name": "div",
                "class_": "volume",
            }
        }

        volume = crawler._build_generated_volume(1, 2)

        self.assertEqual(volume["title"], "Chapters 1 to 2")

    def test_discovers_paginated_chapter_links_from_format_selectors(self):
        crawler = self.make_crawler(r"chapter-\d+")
        crawler.chapter_base_url = "https://novel.example/book"
        crawler.format_data["chapter_list"] = {
            "id": "chapters",
            "name": "div",
            "link": {"name": "a"},
            "pagination": {"name": "ul", "class_": "pagination"},
        }
        crawler.update_log = lambda _message: None
        first_page = """
            <div id="chapters">
                <a href="/book/chapter-1/">One</a>
                <a href="/book/chapter-2/">Two</a>
                <a href="/other/chapter-3/">Unrelated</a>
                <ul class="pagination">
                    <li><a href="/book/page-2/#chapters">2</a></li>
                    <li><a href="/book/page-2/#chapters">Next</a></li>
                </ul>
            </div>
        """
        second_page = """
            <div id="chapters">
                <a href="/book/chapter-3/">Three</a>
                <ul class="pagination"><li><a href="/book/#chapters">1</a></li></ul>
            </div>
        """
        pages = {"https://novel.example/book/page-2/": second_page}
        fetched_pages = []

        def get_page(url, expected_selector=None):
            fetched_pages.append(url)
            return pages[url]

        crawler._get_page_content = get_page

        links = crawler._discover_chapter_links(first_page)

        self.assertEqual(
            links,
            [
                "https://novel.example/book/chapter-1/",
                "https://novel.example/book/chapter-2/",
                "https://novel.example/book/chapter-3/",
            ],
        )
        self.assertEqual(fetched_pages, ["https://novel.example/book/page-2/"])

    def test_discovered_volume_uses_all_links_when_bounds_are_omitted(self):
        crawler = self.make_crawler(r"chapter-\d+")
        links = [f"https://novel.example/book/chapter-{number}/" for number in range(1, 4)]

        volume = crawler._build_discovered_volume(links)

        self.assertEqual(volume["chapter_links"], links)
        self.assertEqual(crawler.novel_info["start_chapter"], 1)
        self.assertEqual(crawler.novel_info["end_chapter"], 3)

    def test_novel_crawler_accepts_root_paginated_chapter_list_template(self):
        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler.url = "https://novel.example/book"
        crawler.base_url = "https://novel.example"
        crawler.format_data = {
            "chapter_list": {
                "id": "chapters",
                "name": "div",
                "link": {"name": "a"},
                "pagination": {"name": "ul", "class_": "pagination"},
            }
        }
        crawler.update_log = lambda _message: None
        first_page = """
            <div id="chapters">
                <a href="/book/chapter-1/">One</a>
                <ul class="pagination"><li><a href="/book/page-2/">2</a></li></ul>
            </div>
        """
        second_page = """
            <div id="chapters">
                <a href="/book/chapter-2/">Two</a>
                <ul class="pagination"><li><a href="/book/">1</a></li></ul>
            </div>
        """
        crawler._get_page_content = lambda url, expected_selector=None: second_page

        self.assertEqual(
            crawler._discover_root_chapter_links(first_page),
            [
                "https://novel.example/book/chapter-1/",
                "https://novel.example/book/chapter-2/",
            ],
        )

    def test_blank_template_click_selectors_are_ignored(self):
        self.assertEqual(
            get_genres(
                None,
                '<a class="genre">Fantasy</a>',
                name="a",
                class_="genre",
                click={"id": "", "name": "", "class_": "", "attrs": ""},
            ),
            ["Fantasy"],
        )
        self.assertEqual(
            get_description(
                None,
                '<div class="description">Summary</div>',
                name="div",
                class_="description",
                text=True,
                click={"id": "", "name": "", "class_": "", "attrs": ""},
            ),
            "Summary",
        )

    def test_blank_template_login_configuration_is_not_enabled(self):
        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler.format_data = {
            "login": {
                "login_btn": {"id": "", "name": "", "class_": "", "attrs": ""},
                "username": {"id": "", "name": "", "class_": "", "attrs": ""},
                "password": {"id": "", "name": "", "class_": "", "attrs": ""},
                "submit_btn": {"id": "", "name": "", "class_": "", "attrs": ""},
            }
        }

        self.assertFalse(crawler._has_login_config())

    def test_missing_chapter_content_selector_raises_clear_error(self):
        with self.assertRaisesRegex(ValueError, "Chapter content not found"):
            get_chapter_content(
                "<html><body><div class='other'>Nope</div></body></html>",
                {"name": "div", "class_": "missing"},
                update_log=lambda _message: None,
            )

    def test_empty_chapter_content_selector_uses_body(self):
        content = get_chapter_content(
            "<html><body><p>Hello</p></body></html>",
            {"id": "", "name": "", "class_": "", "attrs": ""},
            update_log=lambda _message: None,
        )

        self.assertIn("Hello", content)

    def test_empty_chapter_title_selector_uses_page_title(self):
        title = get_chapter_title(
            "<html><head><title>Chapter 1 - Site</title></head><body><p>Body text</p></body></html>",
            {"id": "", "name": "", "class_": "", "attrs": ""},
            update_log=lambda _message: None,
        )

        self.assertEqual(title, "Chapter 1 - Site")

    def test_empty_chapter_image_selector_returns_no_images(self):
        self.assertEqual(
            get_image_urls(
                "<div><p>Text-only chapter</p></div>",
                base_url="https://novel.example",
                name="",
                class_="",
                other_attr="",
            ),
            [],
        )

    def test_malformed_image_tag_is_skipped(self):
        self.assertEqual(
            get_image_urls(
                "<div><img alt='no-src'></div>",
                base_url="https://novel.example",
                name="img",
                other_attr="src",
            ),
            [],
        )

    def test_chapter_retry_helper_skips_after_five_retries(self):
        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler.output_dir = "/tmp/simple_crawler_retry_test"
        crawler.sleep_time = 0
        crawler.update_log_messages = []
        crawler.update_log = crawler.update_log_messages.append
        crawler._get_page_content = lambda _url: "<html><body>debug</body></html>"
        attempts = {"count": 0}

        def fail_chapter(*_args, **_kwargs):
            attempts["count"] += 1
            raise ValueError("broken chapter")

        crawler.crawl_chapter_ = fail_chapter

        with patch("crawler.Novel.time.sleep", return_value=None):
            chapter_data = crawler._crawl_chapter_with_retries(
                "https://novel.example/book/1/",
                img_output_dir="/tmp/simple_crawler_retry_test/img",
                img_prefix="chap1",
                chapter_label="chap1",
                max_retries=5,
            )

        self.assertIsNone(chapter_data)
        self.assertEqual(attempts["count"], 6)
        self.assertTrue(any("Skipping chapter after 5 retries" in message for message in crawler.update_log_messages))

    @unittest.skipIf(importlib.util.find_spec("ebooklib") is None, "ebooklib is not installed")
    def test_chapter_epub_filename_is_short_and_title_stays_readable(self):
        from utils.novel import make_chapter_epub

        long_title = "A Very Long Chapter Title " * 30
        chapter, _images = make_chapter_epub(
            {
                "chapter_title": long_title,
                "chapter_content": "<p>Hello world.</p>",
                "chapter_img_folder": None,
            },
            title_added=("Vol 1", ""),
            idx=7,
            update_log=lambda _message: None,
        )

        self.assertLess(len(chapter.file_name), 80)
        self.assertNotIn("A_Very_Long", chapter.file_name)
        self.assertEqual(chapter.title, "Chapter 7")

    @unittest.skipIf(importlib.util.find_spec("ebooklib") is None, "ebooklib is not installed")
    def test_chapter_epub_title_is_cleaned_from_polluted_page_title(self):
        from utils.novel import make_chapter_epub

        chapter, _images = make_chapter_epub(
            {
                "chapter_title": "Novel Title | Site\nMenu\nOther text\nChương 12: A Clean Title\nSidebar text",
                "chapter_content": "<p>Hello world.</p>",
                "chapter_img_folder": None,
            },
            title_added=("Vol 1", ""),
            idx=12,
            update_log=lambda _message: None,
        )

        self.assertEqual(chapter.title, "Chương 12: A Clean Title")

    @unittest.skipIf(importlib.util.find_spec("ebooklib") is None, "ebooklib is not installed")
    def test_chapter_epub_title_prefers_actual_chapter_over_book_update_marker(self):
        from utils.novel import make_chapter_epub

        chapter, _images = make_chapter_epub(
            {
                "chapter_title": "Novel update Chương 77 END - Chương 2 | Site\nMenu\nChương 2: Real Chapter Title",
                "chapter_content": "<p>Hello world.</p>",
                "chapter_img_folder": None,
            },
            title_added=("Vol 1", ""),
            idx=2,
            update_log=lambda _message: None,
        )

        self.assertEqual(chapter.title, "Chương 2: Real Chapter Title")

    @unittest.skipIf(importlib.util.find_spec("ebooklib") is None, "ebooklib is not installed")
    def test_chapter_epub_title_ignores_long_sidebar_chapter_blob(self):
        from utils.novel import make_chapter_epub

        chapter, _images = make_chapter_epub(
            {
                "chapter_title": "Novel update Chương 77 END | Site\n"
                "Chương 2Tuổi thơ loạn luân – Update Chương 37Chuyện Bí mật của tôi"
                "Những Cuộc Vui dâm đãng Quá Trình Dẫn Dắt Vợ iu: Khiến Vợ Từ Không Ham Muốn",
                "chapter_content": "<p>Hello world.</p>",
                "chapter_img_folder": None,
            },
            title_added=("Vol 1", ""),
            idx=8,
            update_log=lambda _message: None,
        )

        self.assertEqual(chapter.title, "Chapter 8")


if __name__ == "__main__":
    unittest.main()
