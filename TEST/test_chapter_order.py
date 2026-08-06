"""Tests for site-configured chapter-list ordering."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from crawler.Novel import NovelCrawler
from utils.novel import get_all_volume


FOXAHOLIC_STYLE_HTML = """
<div class="listing-chapters_wrap">
  <ul class="main">
    <li><a href="/chapter-epilogue/">Epilogue</a></li>
    <li><a href="/chapter-2/">Chapter 2</a></li>
    <li><a href="/chapter-1/">Chapter 1</a></li>
    <li><a href="/prologue/">Prologue</a></li>
  </ul>
</div>
"""


class TestChapterListOrder(unittest.TestCase):
    def _crawler(self, chapter_list_order=None):
        crawler = NovelCrawler.__new__(NovelCrawler)
        crawler.format_data = {}
        if chapter_list_order is not None:
            crawler.format_data["chapter_list_order"] = chapter_list_order
        crawler.update_log = MagicMock()
        return crawler

    def _newest_first_volume(self):
        return get_all_volume(
            FOXAHOLIC_STYLE_HTML,
            vol_sect={"name": "div", "class_": "listing-chapters_wrap"},
            vol_title={"vol_name": "Chapters"},
            vol_chap={"name": "ul", "class_": "main"},
            base_url="https://www.foxaholic.com",
            update_log=lambda _message: None,
        )

    def test_newest_first_reverses_dom_order_without_sorting_titles(self):
        crawler = self._crawler("newest_first")
        volumes = crawler._normalize_chapter_list_order(self._newest_first_volume())

        self.assertEqual(
            volumes[0]["chapter_links"],
            [
                "https://www.foxaholic.com/prologue/",
                "https://www.foxaholic.com/chapter-1/",
                "https://www.foxaholic.com/chapter-2/",
                "https://www.foxaholic.com/chapter-epilogue/",
            ],
        )
        crawler.update_log.assert_called_once()

    def test_foxaholic_format_declares_newest_first(self):
        format_path = Path("data/formats/foxaholic.json")
        with format_path.open(encoding="utf-8") as format_file:
            format_data = json.load(format_file)

        self.assertEqual(format_data["chapter_list_order"], "newest_first")
        self.assertEqual(format_data["fetch"]["mode"], "auto")
        self.assertFalse(format_data["fetch"]["headless"])

    def test_missing_or_oldest_first_order_keeps_links_unchanged(self):
        original = self._newest_first_volume()
        original_links = list(original[0]["chapter_links"])

        for order in (None, "oldest_first"):
            with self.subTest(order=order):
                crawler = self._crawler(order)
                volumes = crawler._normalize_chapter_list_order(self._newest_first_volume())
                self.assertEqual(volumes[0]["chapter_links"], original_links)
                crawler.update_log.assert_not_called()

    def test_every_volume_is_normalized_before_range_selection(self):
        crawler = self._crawler("newest_first")
        volumes = [
            {"chapter_links": ["v1-latest", "v1-first"]},
            {"chapter_links": ["v2-latest", "v2-first"]},
        ]

        normalized = crawler._normalize_chapter_list_order(volumes)
        flattened = [link for volume in normalized for link in volume["chapter_links"]]

        self.assertEqual(flattened[:2], ["v1-first", "v1-latest"])
        self.assertEqual(flattened[2:], ["v2-first", "v2-latest"])

    def test_invalid_order_is_rejected(self):
        crawler = self._crawler("alphabetical")
        with self.assertRaisesRegex(ValueError, "chapter_list_order"):
            crawler._normalize_chapter_list_order([])
