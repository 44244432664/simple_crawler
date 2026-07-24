import csv
import threading
from urllib.parse import urljoin, urlsplit, urlunsplit

from utils.novel import *
from utils.fetcher import PageFetcher
from crawler.Novel import NovelCrawler, validate_max_workers, DEFAULT_MAX_WORKERS
from utils.worker_config import GlobalPacer, WorkerFetcherFactory, ChapterJob


class XCrawler(NovelCrawler):
    """
    Generic novel crawler for sites whose chapters are reachable by appending a
    predictable chapter postfix to the novel URL.
    """

    def __init__(
        self,
        url=None,
        output_dir=None,
        sleep_time=1000,
        start_chapter=None,
        end_chapter=None,
        keep_logged_in=False,
        driver=False,
        fetch_mode=None,
        headless=None,
        max_workers=None,
    ):
        self.url = self._normalize_url(url or "")
        self.base_url = extract_base_url(self.url) if self.url else ""
        self.output_dir = output_dir + "outputs/Novel/" if output_dir else "outputs/Novel/"
        self.sleep_time = sleep_time / 1000
        self.keep_logged_in = keep_logged_in
        self.fetch_mode = fetch_mode  # None → resolve from format later
        self.headless = headless  # None → resolve from format later
        self.fetcher = None
        self.driver = None
        self.max_workers = validate_max_workers(
            max_workers if max_workers is not None else DEFAULT_MAX_WORKERS
        )
        self._pacer = GlobalPacer(self.sleep_time)
        self._worker_fetcher_factory = None  # Initialized after format_data is loaded
        self._chapter_request_context = threading.local()
        self.start_chapter = start_chapter
        self.end_chapter = end_chapter
        self.site_name = None
        self.title = None
        self.format_data = {}
        self.chapter_base_url = self._url_without_chapter_postfix(self.url)
        self.novel_info = {
            "title": None,
            "author": None,
            "other_info": {},
            "cover_image": None,
            "num_chapters": None,
            "description": None,
            "genres": [],
            "novel_url": self.url,
            "start_chapter": None,
            "end_chapter": None,
            "chapter_links": [],
        }

        if self.url:
            self.site_name = self._site_name_from_aliases()
            with open(self._format_path_for_site_name(self.site_name), "r", encoding="utf-8") as f:
                self.format_data = json.load(f)
            # Apply site format fetch.mode as default when no explicit mode given
            if self.fetch_mode is None:
                self.fetch_mode = self.format_data.get("fetch", {}).get("mode") or "requests"
            self.chapter_base_url = self._url_without_chapter_postfix(self.url)

        # Resolve headless: explicit > site config > True
        if self.headless is None:
            self.headless = self.format_data.get("fetch", {}).get("headless")
        if self.headless is None:
            self.headless = True

        if driver:
            from utils.fetcher import chrome_options
            self.driver = webdriver.Chrome(options=chrome_options(headless=self.headless))

        if not os.path.exists(self.output_dir):
            print("Creating output directory at:", self.output_dir)
            os.makedirs(self.output_dir)

        self.fetch_mode = self.fetch_mode or "requests"
        fetch_config = self.format_data.get("fetch", {})
        self._worker_fetcher_factory = WorkerFetcherFactory(
            cloudflare=fetch_config.get("cloudflare", True),
            challenge_timeout=fetch_config.get("challenge_timeout_seconds", 180),
            profile_name=fetch_config.get("profile_name") or None,
        )
        self.fetcher = PageFetcher(
            fetch_mode=self.fetch_mode,
            cloudflare=fetch_config.get("cloudflare", True),
            challenge_timeout=fetch_config.get("challenge_timeout_seconds", 180),
            profile_name=fetch_config.get("profile_name") or None,
            headless=self.headless,
        )
        print(f"Fetch mode: {self.fetch_mode}")

    @staticmethod
    def _normalize_url(url):
        url = str(url or "").strip()
        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        return url

    @staticmethod
    def _format_path_for_site_name(site_name):
        site_format_path = os.path.join("data", "formats", f"x_{site_name}.json")
        if os.path.exists(site_format_path):
            return site_format_path

        fallback_path = os.path.join("data", "formats", "x.json")
        if os.path.exists(fallback_path):
            return fallback_path

        raise FileNotFoundError(
            f"Could not find format file '{site_format_path}'. "
            "Create data/formats/x_<site_name>.json for XCrawler sites."
        )

    def _site_name_from_aliases(self):
        host = self.base_url.split("//")[-1].split("/")[0].replace("www.", "").rstrip("/")
        with open(os.path.join("data", "aliases.csv"), "r", encoding="utf-8") as aliases_file:
            for row in csv.DictReader(aliases_file):
                site = str(row.get("site") or "").strip().replace("www.", "").rstrip("/")
                if site == host:
                    return row.get("name")
        raise ValueError(f"Site '{host}' was not found in data/aliases.csv.")

    def _clean_selector_args(self, selector):
        return {k: v for k, v in (selector or {}).items() if v != ""}

    def _has_volume_section(self):
        vol_section = self.format_data.get("vol_group", {}).get("vol_section", {})
        return any(value != "" for value in vol_section.values())

    def _chapter_format(self):
        return self.format_data.get("chapter-format", self.format_data.get("chater-format", r"\d+"))

    def _chapter_format_pattern(self):
        chapter_format = self._chapter_format()
        if "{chapter}" in chapter_format:
            return re.escape(chapter_format).replace(re.escape("{chapter}"), r"\d+")
        if r"\d+" in chapter_format:
            prefix, suffix = chapter_format.split(r"\d+", 1)
            return f"{re.escape(prefix)}\\d+{re.escape(suffix)}"
        if r"\d" in chapter_format:
            prefix, suffix = chapter_format.split(r"\d", 1)
            return f"{re.escape(prefix)}\\d+{re.escape(suffix)}"
        return f"{re.escape(chapter_format)}\\d+" if chapter_format else r"\d+"

    def _url_without_chapter_postfix(self, url):
        if not url or not self.format_data:
            return url
        if self._chapter_format().strip().startswith("?"):
            return url

        parsed = urlsplit(url)
        path = parsed.path or "/"
        match = re.search(rf"(?:^|/){self._chapter_format_pattern()}/?$", path)
        if not match:
            return url

        base_path = path[:match.start()].rstrip("/") or "/"
        return urlunsplit((parsed.scheme, parsed.netloc, base_path, "", ""))

    def _resolve_chapter_bounds(self, start_chapter=None, end_chapter=None):
        start = start_chapter if start_chapter is not None else self.start_chapter
        end = end_chapter if end_chapter is not None else self.end_chapter
        if start is None or end is None:
            raise ValueError("XCrawler requires both start_chapter and end_chapter.")
        start = int(start)
        end = int(end)
        if start < 1 or end < 1:
            raise ValueError("Chapter numbers must be positive integers.")
        if end < start:
            raise ValueError("end_chapter must be greater than or equal to start_chapter.")
        self.start_chapter = start
        self.end_chapter = end
        return start, end

    def _chapter_list_config(self):
        """Return configured selectors for a paginated chapter list.

        XCrawler still supports generated URLs for simple sites. A format can
        instead provide ``chapter_list`` with a container selector and optional
        ``link`` / ``pagination`` selectors when the novel page lists chapters.
        """
        config = self.format_data.get("chapter_list") or {}
        if not isinstance(config, dict):
            return {}, {}, {}

        container = config.get("container", config)
        if not isinstance(container, dict):
            container = {}
        container = self._clean_selector_args({
            key: value
            for key, value in container.items()
            if key not in {"link", "pagination", "max_pages"}
        })
        link = self._clean_selector_args(config.get("link") or {"name": "a"})
        pagination = self._clean_selector_args(config.get("pagination") or {})
        return container, link, pagination

    def _has_chapter_list_config(self):
        container, _, _ = self._chapter_list_config()
        return bool(container)

    @staticmethod
    def _canonical_url(url):
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))

    def _is_chapter_link(self, url):
        """Return whether *url* belongs to this novel and matches its format."""
        candidate = urlsplit(url)
        base = urlsplit(self.chapter_base_url)
        if candidate.netloc.lower().removeprefix("www.") != base.netloc.lower().removeprefix("www."):
            return False

        chapter_format = self._chapter_format().strip()
        pattern = self._chapter_format_pattern()
        if chapter_format.startswith("?"):
            return bool(re.fullmatch(pattern, f"?{candidate.query}"))

        base_path = base.path.rstrip("/")
        candidate_path = candidate.path.rstrip("/")
        prefix = f"{base_path}/"
        if not candidate_path.startswith(prefix):
            return False
        chapter_postfix = candidate_path[len(prefix):]
        return bool(re.fullmatch(pattern, chapter_postfix))

    def _discover_chapter_links(self, first_page_html):
        """Discover every configured chapter-list page and return its links.

        Pagination is traversed from page links rather than guessing a URL
        pattern, so formats remain reusable across sites with different routes.
        """
        container_selector, link_selector, pagination_selector = self._chapter_list_config()
        expected_selector = selector_to_css(container_selector)
        max_pages = int(self.format_data.get("chapter_list", {}).get("max_pages", 250))
        first_page_url = self.chapter_base_url
        if not urlsplit(first_page_url).query:
            first_page_url = f"{first_page_url.rstrip('/')}/"
        page_queue = [(first_page_url, first_page_html)]
        seen_pages = set()
        queued_pages = {self._canonical_url(first_page_url)}
        seen_links = set()
        chapter_links = []

        while page_queue and len(seen_pages) < max_pages:
            page_url, page_html = page_queue.pop(0)
            page_url = self._canonical_url(page_url)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)

            soup = BeautifulSoup(page_html, "html.parser")
            chapter_container = soup.find(**container_selector)
            if chapter_container is None:
                self.update_log(f"Chapter-list selector did not match pagination page: {page_url}")
                continue

            for anchor in chapter_container.find_all(**link_selector):
                href = anchor.get("href")
                if not href:
                    continue
                chapter_url = self._canonical_url(urljoin(page_url, href))
                if chapter_url not in seen_links and self._is_chapter_link(chapter_url):
                    seen_links.add(chapter_url)
                    chapter_links.append(chapter_url)

            if not pagination_selector:
                continue
            pagination = chapter_container.find(**pagination_selector)
            if pagination is None:
                continue
            for anchor in pagination.find_all("a", href=True):
                next_page_url = urljoin(page_url, anchor["href"])
                parsed = urlsplit(next_page_url)
                if parsed.scheme not in {"http", "https"}:
                    continue
                next_page_url = self._canonical_url(next_page_url)
                if next_page_url not in seen_pages and next_page_url not in queued_pages:
                    page_queue.append((next_page_url, self._get_page_content(next_page_url, expected_selector)))
                    queued_pages.add(next_page_url)

        if page_queue:
            self.update_log(f"Stopped chapter-list discovery after configured limit of {max_pages} pages.")
        self.update_log(f"Discovered {len(chapter_links)} chapter links across {len(seen_pages)} list pages.")
        return chapter_links

    def _build_discovered_volume(self, chapter_links, start_chapter=None, end_chapter=None):
        if start_chapter is None and end_chapter is None:
            start, end = 1, len(chapter_links)
        else:
            start, end = self._resolve_chapter_bounds(start_chapter, end_chapter)
        selected_links = chapter_links[start - 1:end]
        self.start_chapter = start
        self.end_chapter = start + len(selected_links) - 1 if selected_links else start - 1
        self.novel_info["start_chapter"] = self.start_chapter
        self.novel_info["end_chapter"] = self.end_chapter
        self.novel_info["num_chapters"] = len(selected_links)
        self.novel_info["chapter_links"] = selected_links
        return {"title": "vol_0", "cover_image": None, "chapter_links": selected_links}

    def _chapter_postfix(self, chapter_number):
        chapter_format = self._chapter_format()
        if "{chapter}" in chapter_format:
            postfix = chapter_format.format(chapter=chapter_number)
        elif r"\d+" in chapter_format:
            postfix = chapter_format.replace(r"\d+", str(chapter_number), 1)
        elif r"\d" in chapter_format:
            postfix = chapter_format.replace(r"\d", str(chapter_number), 1)
        else:
            postfix = f"{chapter_format}{chapter_number}" if chapter_format else str(chapter_number)
        return str(postfix)

    def chapter_url(self, chapter_number):
        postfix = self._chapter_postfix(chapter_number).strip()
        if postfix.startswith(("http://", "https://")):
            return postfix
        base_url = self._url_without_chapter_postfix(getattr(self, "chapter_base_url", self.url))
        if postfix.startswith("?"):
            return f"{base_url.rstrip('/')}{postfix}"

        normalized = postfix.strip("/")
        chapter_url = urljoin(f"{base_url.rstrip('/')}/", normalized)
        return chapter_url if chapter_url.endswith("/") else f"{chapter_url}/"

    def chapter_urls(self, start_chapter=None, end_chapter=None):
        start, end = self._resolve_chapter_bounds(start_chapter, end_chapter)
        return [self.chapter_url(chapter) for chapter in range(start, end + 1)]

    def _build_generated_volume(self, start_chapter=None, end_chapter=None):
        start, end = self._resolve_chapter_bounds(start_chapter, end_chapter)
        links = self.chapter_urls(start, end)
        self.novel_info["start_chapter"] = start
        self.novel_info["end_chapter"] = end
        self.novel_info["num_chapters"] = len(links)
        self.novel_info["chapter_links"] = links
        return {
            "title": f"Chapters {start} to {end}" if self._has_volume_section() else "vol_0",
            "cover_image": None,
            "chapter_links": links,
        }

    def get_all_info(self, custom_volume_list=None, info_url=None, start_chapter=None, end_chapter=None):
        if custom_volume_list:
            self.update_log("XCrawler ignores custom volume lists and generates chapter links from chapter-format.")
        if info_url:
            self.url = self._normalize_url(info_url)
            self.chapter_base_url = self._url_without_chapter_postfix(self.url)

        self.novel_info["novel_url"] = self.chapter_base_url
        info_selector = selector_to_css(self.format_data.get("title", {}))
        page_content = self._get_page_content(self.chapter_base_url, expected_selector=info_selector)

        title_args = self._clean_selector_args(self.format_data.get("title", {}))
        self.title = get_title(page_content, **title_args)
        self.output_dir = os.path.join(self.output_dir, self.title)
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            print("Output directory:", self.output_dir)
        self.novel_info["title"] = self.title
        self.update_log(f"Extracted novel title: {self.title}", new=True)

        if self._has_login_config() and self.keep_logged_in:
            self.login_to_site(self.site_name, self.driver.page_source)
        soup = BeautifulSoup(self.driver.page_source, "html.parser") if self.keep_logged_in else BeautifulSoup(page_content, "html.parser")
        html_content = soup.prettify()
        if not html_content:
            self.update_log(f"Failed to retrieve page content from: {self.url}")
            raise Exception("Failed to retrieve page content.")

        cover_args = self._clean_selector_args(self.format_data.get("cover", {}))
        if cover_args:
            referrer = self.format_data.get("img_referrer", False)
            cover_image = get_cover_image_element(
                html_content,
                output_dir=os.path.join(self.output_dir, "img"),
                ext="jpg",
                img_name="cover",
                img_referrer=referrer,
                referer_url=self.chapter_base_url,
                cover_args=cover_args,
                update_log=self.update_log,
            )
        else:
            cover_image = ""
        self.novel_info["cover_image"] = cover_image
        self.update_log(f"Extracted cover image URL: {cover_image}")

        genres_args = self._clean_selector_args(self.format_data.get("genre", {}))
        self.novel_info["genres"] = get_genres(self.driver, html_content, **genres_args) if genres_args else []
        self.update_log(f"Extracted genres: {self.novel_info['genres']}")

        author_args = self._clean_selector_args(self.format_data.get("author", {}))
        author_element = soup.find(**author_args) if author_args else None
        if author_element:
            self.novel_info["author"] = normalize_text(author_element.get_text(" ", strip=True))
        self.update_log(f"Extracted author: {self.novel_info['author']}")

        other_info_format = self.format_data.get("other_info", {})
        holders = self._clean_selector_args(other_info_format.get("holder", {}))
        values = self._clean_selector_args(other_info_format.get("value", {}))
        other_info = get_all_other_novel_info(html_content, holders=holders, values=values) if holders and values else {}
        self.update_log(f"Extracted other info: {other_info}")

        for key, value in other_info.items():
            if key.lower() in ["tac gia", "tác giả", "author"]:
                self.novel_info["author"] = value
            else:
                self.novel_info["other_info"][key] = value
        if not self.novel_info.get("author"):
            self.novel_info["author"] = "Unknown author"

        if not self.novel_info.get("cover_image") or not os.path.exists(self.novel_info["cover_image"]):
            self.update_log("No usable cover found; generating title/author default cover for XCrawler.")
            self.novel_info["cover_image"] = ensure_default_cover_for_novel(
                self.novel_info,
                self.output_dir,
                update_log=self.update_log,
            )

        description_args = self._clean_selector_args(self.format_data.get("description", {}))
        if description_args:
            self.novel_info["description"] = get_description(self.driver, html_content, **description_args)
        else:
            self.novel_info["description"] = ""
        self.update_log(f"Extracted description: {self.novel_info['description'][:100]}...")

        if self._has_chapter_list_config():
            chapter_links = self._discover_chapter_links(page_content)
            chapter_list_order = self.format_data.get("chapter_list_order", "oldest_first")
            if chapter_list_order == "newest_first":
                chapter_links.reverse()
            elif chapter_list_order != "oldest_first":
                raise ValueError(
                    "chapter_list_order must be 'oldest_first' or 'newest_first', "
                    f"got {chapter_list_order!r}"
                )
            volumes = [
                self._build_discovered_volume(
                    chapter_links, start_chapter, end_chapter
                )
            ]
        else:
            self._resolve_chapter_bounds(start_chapter, end_chapter)
            volumes = [self._build_generated_volume()]
        with open(os.path.join(self.output_dir, "novel_info.json"), "w", encoding="utf-8") as f:
            json.dump({"info": self.novel_info, "volumes": volumes}, f, ensure_ascii=False, indent=4)
        return self.novel_info, volumes

    def crawl(self, start_chapter=None, end_chapter=None, info_url=None, custom_volume_list=None):
        novel_info, volumes = self.get_all_info(
            custom_volume_list=custom_volume_list,
            info_url=info_url,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
        )

        volume = volumes[0]
        chapter_links = volume.get("chapter_links", [])
        print(f"Crawling chapters {self.start_chapter} to {self.end_chapter}")

        if not chapter_links:
            volume["chapter_contents"] = []
        else:
            chapter_jobs = [
                ChapterJob(
                    position=idx - self.start_chapter,
                    url=chap_url,
                    img_output_dir=os.path.join(self.output_dir, "img"),
                    img_prefix=f"chap{idx}",
                    label=f"x_chapter_{idx}",
                    max_retries=5,
                )
                for idx, chap_url in enumerate(chapter_links, start=self.start_chapter)
            ]
            volume["chapter_contents"] = self._schedule_chapters(
                chapter_jobs,
                desc="Crawling Generated Chapters",
                unit="chapter",
            )

        with open(os.path.join(self.output_dir, "novel_info.json"), "w", encoding="utf-8") as f:
            json.dump({"info": novel_info, "volumes": volumes}, f, ensure_ascii=False, indent=4)
        self.update_log("XCrawler chapter range completed and data saved.")
        print("XCrawler crawling completed and data saved.")

    def crawl_range(self, start_chapter, end_chapter, custom_volume_list=None, info_url=None):
        self.crawl(
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            info_url=info_url,
            custom_volume_list=custom_volume_list,
        )

    def crawl_chapter(self, chapter, info_url=None):
        if isinstance(chapter, int) or str(chapter).isdigit():
            chapter_url = self.chapter_url(int(chapter))
        else:
            chapter_url = chapter
        super().crawl_chapter(chapter_url, info_url=info_url)
