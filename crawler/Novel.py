from utils.novel import *
from utils.fetcher import PageFetcher
from utils.worker_config import (
    DEFAULT_MAX_WORKERS,
    validate_max_workers,
    log_lock,
    GlobalPacer,
    WorkerFetcherFactory,
    ChapterJob,
    ChapterScheduler,
)
import ast
import threading
import traceback
from urllib.parse import urljoin, urlsplit, urlunsplit


def parse_custom_volume_list(value):
    """Safely normalize an optional list of custom volume URLs.

    Older job files may encode the list as a Python-style literal.  Parse
    those literals without executing them, then require a list of non-empty
    strings before passing it to a crawler.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(
                "custom_volume_list must be a list of volume URL strings"
            ) from exc
    if not isinstance(value, list):
        raise ValueError("custom_volume_list must be a list of volume URL strings")

    normalized = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("custom_volume_list must contain non-empty URL strings")
        normalized.append(item.strip())
    return normalized


class NovelCrawler:
    _supports_parallel = True  # Overridden to False by crawlers that require shared state

    def update_log(self, message, new=False):
        log_file = os.path.join(self.output_dir, "logs", "crawl_log.txt")
        with log_lock:
            if not os.path.exists(os.path.dirname(log_file)):
                os.makedirs(os.path.dirname(log_file))
            if not os.path.exists(log_file):
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write("Novel Crawler Log\n\n")
            if new:
                # delete old log and start new log file
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write("Novel Crawler Log\n\n")
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] {message}\n\n")


    def __init__(self, url=None, output_dir=None, sleep_time=1000, keep_logged_in=False, driver=False, fetch_mode=None, headless=None, max_workers=None):
        self.url = url
        self.base_url = extract_base_url(url)
        # print("Base URL:", self.base_url)
        self.output_dir = output_dir + "outputs/Novel/" if output_dir else "outputs/Novel/"
        self.sleep_time = sleep_time / 1000  # Convert milliseconds to seconds
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

        if url:
            data = pd.read_csv("data/aliases.csv")
            site = self.base_url.split("//")[-1].split("/")[0].replace("www.", "")
            idx = data.index[data['site'] == site]
            self.site_name = data.at[idx[0], 'name'] if not idx.empty else None
            with open(f"data/formats/{self.site_name}.json", 'r', encoding='utf-8') as f:
                self.format_data = json.load(f)

            # Apply site format fetch.mode as default when no explicit mode given
            if self.fetch_mode is None:
                self.fetch_mode = self.format_data.get("fetch", {}).get("mode") or "requests"

            # Resolve headless: explicit > site config > True
            if self.headless is None:
                self.headless = self.format_data.get("fetch", {}).get("headless")
            if self.headless is None:
                self.headless = True

            self.title = None
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

        # Resolve headless for no-url case
        if self.headless is None:
            self.headless = True

        if driver:
            from utils.fetcher import chrome_options
            self.driver = webdriver.Chrome(options=chrome_options(headless=self.headless))

        if not os.path.exists(self.output_dir):
            print("Creating output directory at:", self.output_dir)
            os.makedirs(self.output_dir)

        self.fetch_mode = self.fetch_mode or "requests"
        fetch_config = getattr(self, "format_data", {}).get("fetch", {})
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



    def check_login_btn(self, html_content):
        soup = BeautifulSoup(html_content, 'html.parser')
        # is_log_in_btn = soup.find(class_=self.format_data['login']['login_btn']['class_']) is None
        # print("Checking login button...")
        try:
            is_log_in_btn = soup.find(class_=self.format_data['login']['login_btn']['class_']) is not None
        except Exception as e:
            is_log_in_btn = False
            # print("Error checking login button:", e)
        self.update_log(f"Login button {'found' if is_log_in_btn else 'not found (meaning: logged in)'} on the page.")
        return is_log_in_btn
    
    def login_to_site(self, site_name, html_content):
        login_btn = self.format_data['login']['login_btn']
        user_field = self.format_data['login']['username']
        password_field = self.format_data['login']['password']
        submit_btn = self.format_data['login']['submit_btn']
        logged_in = login(html_content, self.driver, log_btn_args=login_btn, user_field_args=user_field, 
                       pass_field_args=password_field, submit_btn_args=submit_btn, credential_name=site_name,
                       anchor_class=self.format_data["vol_group"]["vol_section"]["class_"])
        # page_log_in = self.driver.page_source
        self.site_name = site_name
        # with open("test_login.html", "w", encoding="utf-8") as f:
        #     f.write(page_log_in)
        self.update_log(f"Login {'successful' if logged_in else 'failed (meaning: logged in)'} for site: {site_name}")
        return logged_in


    def _get_page_content(self, url, expected_selector=None):
        if self.driver:
            return get_page_content(self.driver, url)
        context = getattr(self, "_chapter_request_context", None)
        fetcher = getattr(context, "fetcher", None) if context is not None else None
        if fetcher is not None:
            if getattr(context, "pace_requests", False):
                self._pacer.acquire()
            return fetcher.fetch(url, expected_selector=expected_selector)
        return self.fetcher.fetch(url, expected_selector=expected_selector)

    def _normalize_chapter_list_order(self, volumes):
        """Normalize discovered chapter links to chronological order.

        Site formats list chapters oldest-first unless they explicitly declare
        ``chapter_list_order: newest_first``.  Reversing the extracted list
        keeps non-numeric entries such as prologues and epilogues in their
        intended relative order.
        """
        chapter_list_order = self.format_data.get("chapter_list_order", "oldest_first")
        if chapter_list_order not in {"oldest_first", "newest_first"}:
            raise ValueError(
                "chapter_list_order must be 'oldest_first' or 'newest_first', "
                f"got {chapter_list_order!r}"
            )

        if chapter_list_order == "oldest_first":
            return volumes

        for volume_index, volume in enumerate(volumes, start=1):
            chapter_links = volume.get("chapter_links", [])
            if not chapter_links:
                continue
            volume["chapter_links"] = list(reversed(chapter_links))
            self.update_log(
                f"Reversed chapter links for Volume {volume_index} "
                "because chapter_list_order is newest_first."
            )
        return volumes

    @staticmethod
    def _clean_selector_args(selector, excluded=()):
        """Remove blank and format-only values before BeautifulSoup lookup."""
        return {
            key: value
            for key, value in (selector or {}).items()
            if key not in excluded and value != ""
        }

    def _root_chapter_list_config(self):
        """Return root chapter-list selectors from the current site template."""
        config = self.format_data.get("chapter_list") or {}
        if not isinstance(config, dict):
            return {}, {}, {}
        container = config.get("container", config)
        if not isinstance(container, dict):
            container = {}
        container = self._clean_selector_args(
            container, excluded={"link", "pagination", "max_pages"}
        )
        link = self._clean_selector_args(config.get("link") or {"name": "a"})
        pagination = self._clean_selector_args(config.get("pagination") or {})
        return container, link, pagination

    def _has_login_config(self):
        """Return whether a format contains usable login selectors.

        The current template includes blank login fields as placeholders; those
        must not trigger an attempted login just because the mapping exists.
        """
        login = self.format_data.get("login") or {}
        return any(
            isinstance(selector, dict)
            and any(value != "" for value in selector.values())
            for selector in login.values()
        )

    @staticmethod
    def _canonical_page_url(url):
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))

    def _discover_root_chapter_links(self, first_page_html):
        """Discover chapters from a template root ``chapter_list`` selector.

        This is used by non-X crawlers whose site format has no volume section.
        Chapter anchors inside the configured pagination control are excluded.
        """
        container_selector, link_selector, pagination_selector = self._root_chapter_list_config()
        if not container_selector:
            return []

        config = self.format_data.get("chapter_list", {})
        max_pages = int(config.get("max_pages", 250))
        expected_selector = selector_to_css(container_selector)
        first_page_url = self.url
        if not urlsplit(first_page_url).query:
            first_page_url = f"{first_page_url.rstrip('/')}/"
        page_queue = [(first_page_url, first_page_html)]
        seen_pages = set()
        queued_pages = {self._canonical_page_url(first_page_url)}
        seen_chapters = set()
        chapter_links = []
        base_host = urlsplit(self.base_url).netloc.lower().removeprefix("www.")

        while page_queue and len(seen_pages) < max_pages:
            page_url, page_html = page_queue.pop(0)
            page_url = self._canonical_page_url(page_url)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)

            soup = BeautifulSoup(page_html, "html.parser")
            container = soup.find(**container_selector)
            if container is None:
                self.update_log(f"Chapter-list selector did not match pagination page: {page_url}")
                continue

            pagination = container.find(**pagination_selector) if pagination_selector else None
            pagination_links = {
                id(anchor) for anchor in pagination.find_all("a", href=True)
            } if pagination else set()
            for anchor in container.find_all(**link_selector):
                href = anchor.get("href")
                if not href or id(anchor) in pagination_links:
                    continue
                chapter_url = self._canonical_page_url(urljoin(page_url, href))
                chapter_host = urlsplit(chapter_url).netloc.lower().removeprefix("www.")
                if chapter_host != base_host or chapter_url in seen_chapters:
                    continue
                seen_chapters.add(chapter_url)
                chapter_links.append(chapter_url)

            if not pagination:
                continue
            for anchor in pagination.find_all("a", href=True):
                next_page_url = urljoin(page_url, anchor["href"])
                parsed = urlsplit(next_page_url)
                if parsed.scheme not in {"http", "https"}:
                    continue
                next_page_url = self._canonical_page_url(next_page_url)
                if next_page_url not in seen_pages and next_page_url not in queued_pages:
                    page_queue.append((next_page_url, self._get_page_content(
                        next_page_url, expected_selector=expected_selector
                    )))
                    queued_pages.add(next_page_url)

        if page_queue:
            self.update_log(f"Stopped chapter-list discovery after configured limit of {max_pages} pages.")
        self.update_log(f"Discovered {len(chapter_links)} root chapter links across {len(seen_pages)} list pages.")
        return chapter_links

    def close(self):
        """Release all resources after a crawl has completed.

        This is safe to call from the command runner's ``finally`` block or
        repeatedly by callers that end a crawl early.
        """
        if hasattr(self, '_worker_fetcher_factory') and self._worker_fetcher_factory:
            self._worker_fetcher_factory.close_all()
        if hasattr(self, '_pacer') and self._pacer:
            self._pacer.close()
        if hasattr(self, 'fetcher') and self.fetcher:
            self.fetcher.close()
        if hasattr(self, 'driver') and self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                self.driver = None

    def _create_worker_fetcher(self):
        """Create a requests-only PageFetcher for a worker thread.

        The fetcher inherits cloudflare/retry configuration from the parent
        but is always in ``"requests"`` mode.  It is tracked centrally and
        closed automatically when :meth:`close` is called.
        """
        if self._worker_fetcher_factory is None:
            raise RuntimeError(
                "Worker fetcher factory is not initialized. "
                "Ensure the crawler has a valid URL and format_data."
            )
        return self._worker_fetcher_factory.get_fetcher()

    def _run_chapter_job(self, job, fetcher, pace_requests=True):
        """Run one chapter with a thread-local fetcher and pacing policy."""
        context = getattr(self, "_chapter_request_context", None)
        if context is None:
            context = threading.local()
            self._chapter_request_context = context
        context.fetcher = fetcher
        context.pace_requests = pace_requests
        try:
            return self._crawl_chapter_with_retries(
                job.url,
                img_output_dir=job.img_output_dir,
                img_prefix=job.img_prefix,
                chapter_label=job.label,
                max_retries=job.max_retries,
            )
        finally:
            context.fetcher = None
            context.pace_requests = False

    
    def get_all_info(self, custom_volume_list=None, info_url=None):
        # custom_volume_list: list of volume links to crawl instead of extracting from page
        if custom_volume_list and info_url:
            self.url = info_url

        # A title can be too broad to distinguish a generic block page from
        # the actual novel.  Formats may provide a more specific ready marker
        # for Cloudflare/browser recovery while title parsing still uses the
        # normal ``title`` selector below.
        info_selector = selector_to_css(self.format_data.get("info_page_ready", {}))
        if not info_selector:
            info_selector = selector_to_css(self.format_data.get("title", {}))
        page_content = self._get_page_content(
            self.url if not info_url else info_url,
            expected_selector=info_selector,
        )

        # get novel title
        title_args = {k: v for k, v in self.format_data['title'].items() if v!=""}
        self.title = get_title(page_content, **title_args)
        self.output_dir = os.path.join(self.output_dir, self.title)
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            print("Output directory:", self.output_dir)
            self.update_log(f"Output directory created at: {self.output_dir}")
        self.novel_info['title'] = self.title
        self.update_log(f"Extracted novel title: {self.title}", new=True)

        if self._has_login_config() and self.keep_logged_in:
            self.login_to_site(self.site_name, self.driver.page_source)
        soup = BeautifulSoup(self.driver.page_source, 'html.parser') if self.keep_logged_in else BeautifulSoup(page_content, 'html.parser')
        html_content = soup.prettify()
        # print("Fetching novel information from:", self.url)
        # print("Page content fetched.")

        # html_content = get_page_content(self.driver, self.url)
        print("Fetching novel information from:", self.url)
        
        # print(BeautifulSoup(html_content, 'html.parser').prettify())
        if not html_content:
            self.update_log(f"Failed to retrieve page content from: {self.url}")
            raise Exception("Failed to retrieve page content.")

        print("Page content fetched.")
        self.update_log(f"Fetched novel information page: {self.url}")

        # get cover image
        cover_args = {k: v for k, v in self.format_data['cover'].items() if v!=""}
        referrer = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
        cover_image_url = get_cover_image_element(html_content, 
                                                  output_dir=self.output_dir+"/img", ext="jpg",
                                                  img_name="cover", img_referrer=referrer,
                                                  referer_url=self.url, cover_args=cover_args, update_log=self.update_log)
        self.novel_info['cover_image'] = cover_image_url
        
        self.update_log(f"Extracted cover image URL: {cover_image_url}")
        
        # get genres
        genres_args = {k: v for k, v in self.format_data['genre'].items() if v!=""}
        self.novel_info['genres'] = get_genres(self.driver, html_content, **genres_args)
        
        self.update_log(f"Extracted genres: {self.novel_info['genres']}")

        holders = {k: v for k, v in self.format_data['other_info']['holder'].items() if v!=""}
        values = {k: v for k, v in self.format_data['other_info']['value'].items() if v!=""}
        other_info = get_all_other_novel_info(html_content, holders=holders, values=values)
        
        self.update_log(f"Extracted other info: {other_info}")
        
        for key, value in other_info.items():
            if key.lower() == 'tác giả' or key.lower() == 'author':
                self.novel_info['author'] = value
            # can add more info extraction here if needed
            else:
                self.novel_info["other_info"][key] = value
                # self.novel_info[key] = value

        if not self.novel_info.get("cover_image") or not os.path.exists(self.novel_info["cover_image"]):
            self.update_log(
                "Default cover fallback review: scraped cover is missing; requirements are title/author rendering, "
                "decorative deterministic PNG output, and EPUB compatibility. Mitigation: generate cover in the novel img directory."
            )
            self.novel_info["cover_image"] = ensure_default_cover_for_novel(
                self.novel_info,
                self.output_dir,
                update_log=self.update_log,
            )

        description_args = {k: v for k, v in self.format_data['description'].items() if v!=""}
        description = get_description(self.driver, html_content, **description_args)
        self.novel_info['description'] = description

        self.update_log(f"Extracted description: {description[:100]}...")  # Log the first 100 characters of the description

        if custom_volume_list:
            self.update_log(f"Using custom volume list with {len(custom_volume_list)} volumes.")
            volume_links = custom_volume_list
            
            vol_page = self.format_data['vol_page']
            vol_title = {k: v for k, v in vol_page['vol_title'].items() if v!=""}
            if 'vol_cover' in vol_page:
                vol_cover = {k: v for k, v in vol_page['vol_cover'].items() if v!=""}
                vol_cover['output_dir'] = self.output_dir+"/img"
                vol_cover['img_referrer'] = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
            else:
                vol_cover = {}
            vol_chap = {k: v for k, v in vol_page['chapter_list'].items() if v!=""}
            volumes = []
            for i, link in enumerate(volume_links):
                if vol_cover:
                    vol_cover['img_name'] = f"vol_{i+1}_cover"
                    # self.update_log(f"Found volume cover for volume {i+1}")
                # print(f"vol cover args for custom volumes: {vol_cover}")
                vol_info = get_volume_info_from_link(self.driver, base_url=self.base_url,
                                                    vol_url=link, 
                                                    title_args=vol_title,
                                                    cover_args=vol_cover,
                                                    chap_list_args=vol_chap,
                                                    update_log=self.update_log,
                                                    page_fetcher=self._get_page_content)
                volumes.append(vol_info)
                self.update_log(f"Extracted volume info from link: {link} with title: {vol_info['title']}")
                time.sleep(1)  # sleep between volume requests
        else:
            self.update_log("Using automatic volume extraction from the main page.")
            html_content = self._get_page_content(
                self.url,
                expected_selector=selector_to_css(self.format_data.get("title", {})),
            )

            group = self.format_data.get('vol_group', {})
            vol_section = self._clean_selector_args(group.get('vol_section', {}))
            if vol_section:
                vol_title = self._clean_selector_args(group.get('vol_title', {}))
                vol_cover = self._clean_selector_args(group.get('vol_cover', {}))
                if vol_cover:
                    vol_cover['output_dir'] = self.output_dir+"/img"
                vol_chap = self._clean_selector_args(group.get('chapter_list', {}))
                volumes = get_all_volume(html_content, vol_sect=vol_section,
                                        vol_title=vol_title,
                                        vol_cover=vol_cover,
                                        vol_chap=vol_chap,
                                        base_url=self.base_url,
                                        update_log=self.update_log)
                self.update_log(f"Extracted information for {len(volumes)} volumes from the main page.")
            else:
                chapter_links = self._discover_root_chapter_links(html_content)
                if not chapter_links:
                    raise ValueError(
                        "Format needs either vol_group.vol_section or a root chapter_list selector."
                    )
                volumes = [{
                    "title": "vol_0",
                    "cover_image": None,
                    "chapter_links": chapter_links,
                }]
                self.update_log(
                    f"Extracted {len(chapter_links)} chapters from the root chapter list."
                )
        volumes = self._normalize_chapter_list_order(volumes)
        self.novel_info["chapter_links"] = [
            chapter_url
            for volume in volumes
            for chapter_url in volume.get("chapter_links", [])
        ]
        print("Got all novel information.")
        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({"info" : self.novel_info, "volumes": volumes}, f, ensure_ascii=False, indent=4)
        return self.novel_info, volumes


    def crawl_chapter_(self, chapter_url, img_output_dir=None, img_prefix=""):
        try:
            logged_in = not self.keep_logged_in  # If we don't need to keep logged in, we can skip login check
            if self.driver and self.keep_logged_in:
                self.driver.get(chapter_url)
                self.driver.implicitly_wait(10)
                # logged_in = True

            # if self.format_data.get('login', None):
                logged_in = self.check_login_btn(self.driver.page_source)
            if not logged_in:
                self.login_to_site(self.site_name, self.driver.page_source)
                time.sleep(3)  # wait for login to process
            # print("logged in status:", logged_in, "keep_logged_in:", self.keep_logged_in)
            content_from = "driver" if self.keep_logged_in else "requests"
            if self.keep_logged_in:
                chapter_content = self.driver.page_source
            else:
                chap_selector = selector_to_css(self.format_data.get("chapter", {}).get("content", {}))
                chapter_content = self._get_page_content(chapter_url, expected_selector=chap_selector)
            # print(f"Fetched chapter content from {content_from} for URL: {chapter_url}")
        except Exception as e:
            self.update_log(f"Error fetching chapter content from {chapter_url}: {e} with status code {self.driver.status_code if self.driver else 'No driver'}")
            print(f"Error fetching chapter content from {chapter_url}: {e}", self.driver.status_code if self.driver else "No driver")
            print("Retrying...")
            for _attempt in range(5):
                time.sleep(2)  # wait before retrying
                if self.driver and self.keep_logged_in:
                    self.driver.get(chapter_url)
                    self.driver.implicitly_wait(10)
                    logged_in = self.check_login_btn(self.driver.page_source) if self._has_login_config() else True
                    if not logged_in:
                        self.login_to_site(self.site_name, self.driver.page_source)
                if self.keep_logged_in:
                    chapter_content = self.driver.page_source
                else:
                    chap_selector = selector_to_css(self.format_data.get("chapter", {}).get("content", {}))
                    chapter_content = self._get_page_content(chapter_url, expected_selector=chap_selector)
                if chapter_content:
                    break
            else:
                print(f"Failed to retrieve chapter content from {chapter_url} after retries.")
                return None
        if not chapter_content:
            self.update_log(f"Failed to retrieve chapter content from {chapter_url}")
            print(f"Failed to retrieve chapter content from {chapter_url}")
            return None
        chapter_prop = self.format_data['chapter']
        chapter_title = get_chapter_title(chapter_content, chapter_prop['title'], update_log=self.update_log)
        chapter_body = get_chapter_content(chapter_content, chapter_prop['content'], update_log=self.update_log)
        soup = BeautifulSoup(chapter_body, 'html.parser')

        removed_elements = 0
        for remove_selector in chapter_prop.get("remove", []):
            if not isinstance(remove_selector, dict):
                continue
            class_prefix = remove_selector.get("class_prefix", "")
            id_prefix = remove_selector.get("id_prefix", "")
            selector_args = {
                key: value
                for key, value in remove_selector.items()
                if key not in {"class_prefix", "id_prefix"} and value != ""
            }
            elements = soup.find_all(**selector_args) if selector_args else []
            if class_prefix:
                elements.extend(
                    tag for tag in soup.find_all(True)
                    if any(str(css_class).startswith(class_prefix) for css_class in tag.get("class", []))
                )
            if id_prefix:
                elements.extend(
                    tag for tag in soup.find_all(True)
                    if str(tag.get("id", "")).startswith(id_prefix)
                )
            seen = set()
            for element in elements:
                element_id = id(element)
                if element_id not in seen:
                    seen.add(element_id)
                    element.decompose()
                    removed_elements += 1
        if removed_elements:
            self.update_log(f"Removed {removed_elements} configured non-content/ad elements from chapter HTML.")

        self.update_log(f"Extracted chapter title: {chapter_title} and content (text only) from URL: {chapter_url}")

        image_prop = chapter_prop.get('image', {})
        allowed_image_hosts = image_prop.get("allowed_hosts")
        img_args = {
            k: v
            for k, v in image_prop.items()
            if k not in ["delete", "allowed_hosts"] and v != ""
        }
        if img_args.get("other_attr") and any(k != "other_attr" for k in img_args):
            image_attr = img_args["other_attr"]
            imgs = soup.find_all(**{k: v for k, v in img_args.items() if k not in ['other_attr'] and v!=""})
            image_entries = []
            for img in imgs:
                raw_url = img.get(image_attr)
                if not raw_url:
                    continue
                raw_url = raw_url.strip()
                if raw_url.lower().startswith(("data:", "javascript:", "about:blank", "#")):
                    continue
                image_entries.append((img, urljoin(self.base_url, raw_url)))
        else:
            image_entries = []
            self.update_log("No chapter image selector configured; skipping image extraction.")
        if allowed_image_hosts:
            allowed_image_hosts = set(allowed_image_hosts)
            filtered_image_entries = [
                entry
                for entry in image_entries
                if urlparse(entry[1]).hostname in allowed_image_hosts
            ]
            skipped_images = len(image_entries) - len(filtered_image_entries)
            image_entries = filtered_image_entries
            if skipped_images:
                self.update_log(
                    f"Skipped {skipped_images} chapter images from untrusted hosts."
                )
        # print(f"image to delete: {chapter_prop['image']['delete']}, total images found: {len(img_src)}")
        if 'delete' in image_prop and image_prop['delete'] and image_entries:
            delete_count = min(abs(image_prop['delete']), len(image_entries))
            for i in range(delete_count):
                image_entries.pop()[0].decompose()
            self.update_log(f"Deleted {delete_count} images in chapter content (set as ad).")
        chapter_img_folder = None
        browser_cookies = None
        browser = getattr(getattr(self, "fetcher", None), "_driver", None)
        if browser is not None:
            try:
                browser_cookies = {
                    cookie["name"]: cookie["value"]
                    for cookie in browser.get_cookies()
                    if cookie.get("name") and cookie.get("value")
                }
            except Exception:
                # Images can still be downloaded using the normal referrer
                # path when the browser does not expose its cookies.
                browser_cookies = None
        for i, (img, img_url) in enumerate(image_entries):
            img_name = f"{img_prefix}_img{i+1}"
            local_img_path = download_image(img_url, output_dir=img_output_dir, ext="jpg", name=img_name,
                                        img_referrer=self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False, 
                                        update_log=self.update_log, referer_url=chapter_url,
                                        cookies=browser_cookies, browser_driver=browser)
            if local_img_path:
                # Keep the parsed tag paired with its normalized download URL.
                # This works for Foxaholic's relative ``/wp-content/...``
                # sources as well as absolute URLs.
                img[image_attr] = local_img_path
                chapter_img_folder = os.path.dirname(local_img_path)
            else:
                self.update_log(
                    f"Keeping unresolved image URL in chapter content: {img_url}"
                )

            # self.update_log(f"Downloaded image from {img_url} to {local_img_path} and updated chapter content.")

        chapter_body = soup.prettify()

        self.update_log(f"Finished processing chapter from URL: {chapter_url} with title: {chapter_title}")
        return {
            "chapter_title": chapter_title,
            "chapter_content": chapter_body,
            "chapter_img_folder": chapter_img_folder
        }

    def _save_debug_chapter_source(self, chapter_url, chapter_label):
        log_dir = os.path.join(self.output_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        debug_path = os.path.join(log_dir, f"debug_{chapter_label}.html")
        try:
            source = self._get_page_content(chapter_url)
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(BeautifulSoup(str(source), "html.parser").prettify())
            self.update_log(f"Saved debug page source for skipped chapter to: {debug_path}")
        except Exception as debug_error:
            self.update_log(
                f"Could not save debug page source for skipped chapter {chapter_url}: "
                f"{type(debug_error).__name__}: {debug_error}\n{traceback.format_exc()}"
            )
        return debug_path

    def _crawl_chapter_with_retries(self, chapter_url, img_output_dir=None, img_prefix="", chapter_label=None, max_retries=5):
        chapter_label = chapter_label or img_prefix or "chapter"
        total_attempts = max_retries + 1

        for attempt in range(1, total_attempts + 1):
            try:
                chapter_data = self.crawl_chapter_(
                    chapter_url,
                    img_output_dir=img_output_dir,
                    img_prefix=img_prefix,
                )
                if chapter_data:
                    if attempt > 1:
                        self.update_log(
                            f"Chapter recovered after retry. URL: {chapter_url}. "
                            f"Successful attempt: {attempt}/{total_attempts}."
                        )
                    return chapter_data

                message = (
                    f"Chapter crawl returned no data. URL: {chapter_url}. "
                    f"Attempt {attempt}/{total_attempts}."
                )
                self.update_log(message)
                print(message)
            except KeyboardInterrupt:
                self.update_log("Crawling interrupted by user.")
                raise
            except Exception as error:
                error_detail = traceback.format_exc()
                self.update_log(
                    f"Error crawling chapter. URL: {chapter_url}. "
                    f"Attempt {attempt}/{total_attempts}. "
                    f"Error: {type(error).__name__}: {error}\n{error_detail}"
                )
                print(f"Error crawling chapter {chapter_url} on attempt {attempt}/{total_attempts}: {error}")

            if attempt < total_attempts:
                print("Retrying...")
                time.sleep(2)

        debug_path = self._save_debug_chapter_source(chapter_url, chapter_label)
        self.update_log(
            f"Skipping chapter after {max_retries} retries. URL: {chapter_url}. "
            f"Debug source path: {debug_path}"
        )
        print(f"Skipping chapter after {max_retries} retries: {chapter_url}")
        return None

    
    def _make_chapter_work_fn(self):
        """Return a work function suitable for :class:`ChapterScheduler`.

        The returned callable acquires the global pacer before each
        chapter fetch and delegates to :meth:`_crawl_chapter_with_retries`.
        """
        def _work(job: ChapterJob):
            return self._run_chapter_job(job, self._create_worker_fetcher())
        return _work

    def _is_parallel_safe(self):
        """Return True if this crawler instance can safely use threaded chapter fetching.

        Parallel execution is enabled only when all of the following hold:
        - The crawler class supports independent request sessions.
        - The resolved fetch mode is exactly ``"requests"``.
        - No persistent login is requested.
        - No Selenium driver is active.
        """
        if not self._supports_parallel:
            return False
        if getattr(self, "fetch_mode", None) != "requests":
            return False
        if getattr(self, "keep_logged_in", False):
            return False
        if getattr(self, "driver", None) is not None:
            return False
        return True

    def _crawl_chapters_sequentially(self, chapter_jobs, desc="Crawling Chapters", unit="chapter"):
        """Crawl *chapter_jobs* one at a time in source order.

        This is the fallback path used when parallel execution is not safe
        or when ``max_workers`` is 1.  It preserves all retry, pacing, and
        error-handling behaviour of the threaded path.
        """
        results = []
        pbar = None
        try:
            import tqdm as _tqdm
            pbar = _tqdm.tqdm(total=len(chapter_jobs), desc=desc, unit=unit)
        except ImportError:
            pass

        try:
            for job in chapter_jobs:
                result = self._run_chapter_job(
                    job,
                    self.fetcher,
                    pace_requests=self.driver is None,
                )
                if result:
                    results.append(result)
                if pbar is not None:
                    pbar.update(1)
        finally:
            if pbar is not None:
                pbar.close()
        return results

    def _schedule_chapters(self, chapter_jobs, desc="Crawling Chapters", unit="chapter"):
        """Submit *chapter_jobs* concurrently and return results in source order.

        This is the single point of concurrency for all crawl paths.
        It builds a :class:`ChapterScheduler` with the crawler's
        ``max_workers``, wires in the global pacer, and returns the
        ordered list of successful chapter payloads.

        When parallel execution is not safe (browser/auto mode, persistent
        login, active driver, or crawler without independent-session support)
        the method falls back to a sequential for-loop that preserves all
        retry, pacing, and error-handling behaviour.

        Parameters
        ----------
        chapter_jobs : list[ChapterJob]
            Ordered chapter descriptors.
        desc : str
            Progress-bar description prefix.
        unit : str
            Progress-bar unit label.

        Returns
        -------
        list[dict]
            Successful chapter payloads in source order.
        """
        if not chapter_jobs:
            return []

        if self.max_workers == 1 or not self._is_parallel_safe():
            if self.max_workers > 1 and not self._is_parallel_safe():
                reasons = []
                if getattr(self, "fetch_mode", None) != "requests":
                    reasons.append(f"fetch_mode={self.fetch_mode!r}")
                if getattr(self, "keep_logged_in", False):
                    reasons.append("keep_logged_in=True")
                if getattr(self, "driver", None) is not None:
                    reasons.append("driver=active")
                if not self._supports_parallel:
                    reasons.append("crawler does not support independent sessions")
                reason_str = "; ".join(reasons) if reasons else "unknown"
                msg = (
                    f"Parallel crawling disabled ({reason_str}): "
                    f"falling back to sequential execution."
                )
                print(msg)
                self.update_log(msg)
            return self._crawl_chapters_sequentially(chapter_jobs, desc, unit)

        work_fn = self._make_chapter_work_fn()
        total = len(chapter_jobs)

        pbar = None
        try:
            import tqdm as _tqdm
            pbar = _tqdm.tqdm(total=total, desc=desc, unit=unit)
        except ImportError:
            pass

        def _on_progress(completed, _total):
            if pbar is not None:
                pbar.update(1)

        def _on_error(job, error):
            self.update_log(
                f"Unhandled chapter worker error. URL: {job.url}. "
                f"Error: {type(error).__name__}: {error}"
            )

        scheduler = ChapterScheduler(
            work_fn=work_fn,
            chapters=chapter_jobs,
            max_workers=self.max_workers,
            progress_fn=_on_progress,
            error_fn=_on_error,
        )
        try:
            results = scheduler.run()
        finally:
            if pbar is not None:
                pbar.close()
            # The scheduler has fully stopped before this point, so per-thread
            # sessions can be closed before starting the next volume.
            self._worker_fetcher_factory.close_all()

        return results

    def crawl(self, custom_volume_list=None, info_url=None):
        novel_info, volumes = self.get_all_info(custom_volume_list=custom_volume_list, info_url=info_url)
        for idx, vol in enumerate(volumes):
            chapter_links = vol.get('chapter_links', [])
            self.update_log(f"Starting to crawl Volume {idx+1}: {vol['title']} with {len(chapter_links)} chapters.")
            print(f"Crawling Volume {idx+1}: {vol['title']}")

            if not chapter_links:
                vol['chapter_contents'] = []
                continue

            chapter_jobs = [
                ChapterJob(
                    position=jdx,
                    url=chap_url,
                    img_output_dir=self.output_dir + "/img",
                    img_prefix=f"vol{idx+1}_chap{jdx+1}",
                    label=f"vol{idx+1}_chapter_{jdx+1}",
                    max_retries=5,
                )
                for jdx, chap_url in enumerate(chapter_links)
            ]

            vol['chapter_contents'] = self._schedule_chapters(
                chapter_jobs,
                desc=f"Crawling Volume {idx+1} Chapters",
                unit="chapter",
            )

        info = {"info" : novel_info}
        all_vol = {"volumes": volumes}

        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
        self.update_log(f"Crawling all chapters completed and data saved.")
        print("Crawling completed and data saved.")


    
    def crawl_range(self, start_chapter, end_chapter, custom_volume_list=None, info_url=None):
        novel_info, volumes = self.get_all_info(custom_volume_list=custom_volume_list, info_url=info_url)
        flattened_chapter_links = []
        chapter_map = []  # To keep track of which chapter belongs to which volume
        for vol_idx, vol in enumerate(volumes):
            for chap_url in vol['chapter_links']:
                flattened_chapter_links.append(chap_url)
                chapter_map.append(vol_idx)
        selected_chapter_links = flattened_chapter_links[start_chapter-1:end_chapter]

        if not selected_chapter_links:
            all_chapters = []
        else:
            chapter_jobs = [
                ChapterJob(
                    position=i,
                    url=chap_url,
                    img_output_dir=self.output_dir + "/img",
                    img_prefix=f"vol{chapter_map[start_chapter - 1 + i]+1}_chap{start_chapter + i}",
                    label=f"range_chapter_{start_chapter + i}",
                    max_retries=5,
                )
                for i, chap_url in enumerate(selected_chapter_links)
            ]
            all_chapters = self._schedule_chapters(
                chapter_jobs,
                desc="Crawling Selected Chapters",
                unit="chapters",
            )

        vol_0 = {
            "title": f"Chapters {start_chapter} to {end_chapter}",
            "cover_image": None,
            "chapter_links": selected_chapter_links,
            "chapter_contents": all_chapters
        }
        info = {"info" : novel_info}
        all_vol = {"volumes": [vol_0]}
        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
        self.update_log(f"Crawling of selected chapter range ({start_chapter} to {end_chapter}) completed and data saved.")
        print("Crawling of selected chapter range completed and data saved.")


    def crawl_chapter(self, chapter_url, info_url=None):
        novel_info, volumes = self.get_all_info(info_url=info_url) if info_url else self.get_all_info()
        chapter_data = self.crawl_chapter_(chapter_url, img_output_dir=self.output_dir+"/img", 
                                          img_prefix="single_chap")
        vol_0 = {
            "title": "Single Chapter Crawl",
            "cover_image": None,
            "chapter_links": [chapter_url],
            "chapter_contents": [chapter_data] if chapter_data else []
        }
        info = {"info" : novel_info}
        all_vol = {"volumes": [vol_0]}
        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
        self.update_log(f"Crawling of single chapter from URL: {chapter_url} completed and data saved.")
        print("Crawling of single chapter completed and data saved.")
    
    def make_epub(self, novel_info_path=None):
        # make epub from crawled data in novel_info.json
        # self.output_dir = os.path.join(self.output_dir, "Hội chứng muốn sống bình an tại dị giới")
        self.output_dir = novel_info_path if novel_info_path else self.output_dir
        novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
        if not os.path.exists(novel_info_path):
            self.update_log("Novel information file not found.")
            print("Novel information file not found. Please run the crawler first.")
            return
        with open(novel_info_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        novel_info = data['info']
        volumes = data['volumes']
        if not novel_info.get("cover_image") or not os.path.exists(novel_info["cover_image"]):
            self.update_log(
                "EPUB cover fallback review: existing novel_info has no usable cover; generating deterministic PNG before compilation."
            )
            ensure_default_cover_for_novel(
                novel_info,
                self.output_dir,
                update_log=self.update_log,
                persist_path=novel_info_path,
            )

        # make volumes epub elements
        volume_list = []
        for idx, vol in enumerate(volumes):
            self.update_log(f"Preparing Volume {idx+1} for EPUB: {vol['title']}")
            print(f"Preparing Volume: {vol['title']}")
            chapter_elements = []
            for chap_idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
                                     desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
                c = make_chapter_epub(chapter, title_added=(f"Vol {idx+1}", ""), idx=chap_idx+1, update_log=self.update_log)
                chapter_elements.append(c)
            v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
            self.update_log(f"Volume {idx+1} prepared for EPUB with title: {vol['title']}")
            volume_list.append((v_img, v, c_lst))
        # create epub
        make_book_epub(novel_info, volume_list, ("",""), output_path=self.output_dir, ebook_name=novel_info['title'] + ".epub")
        self.update_log(f"EPUB creation completed for novel: {novel_info['title']} with {len(volumes)} volumes.")
        

    
    def make_volume_epub(self, novel_info_path=None):
        self.output_dir = novel_info_path if novel_info_path else self.output_dir
        novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
        if not os.path.exists(novel_info_path):
            self.update_log("Novel information file not found.")
            print("Novel information file not found. Please run the crawler first.")
            return
        with open(novel_info_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        novel_info = data['info']
        volumes = data['volumes']
        if not novel_info.get("cover_image") or not os.path.exists(novel_info["cover_image"]):
            self.update_log(
                "Volume EPUB cover fallback review: existing novel_info has no usable cover; generating deterministic PNG before compilation."
            )
            ensure_default_cover_for_novel(
                novel_info,
                self.output_dir,
                update_log=self.update_log,
                persist_path=novel_info_path,
            )

        for i, vol in enumerate(volumes):
            print(f"Preparing Volume: {vol['title']}")
            chapter_elements = []
            for chap_idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
                                     desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
                c = make_chapter_epub(chapter, title_added=("", ""), idx=chap_idx+1, update_log=self.update_log)
                chapter_elements.append(c)
            v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
            # create epub for each volume
            self.update_log(f"Creating EPUB for Volume: {vol['title']}")
            make_book_epub(novel_info, [(v_img, v, c_lst)], (f"vol {i+1}",""), output_path=self.output_dir, ebook_name=novel_info['title'] + f" vol {i+1} - {vol['title']}.epub")
            self.update_log(f"EPUB creation completed for Volume: {vol['title']} with {len(vol['chapter_contents'])} chapters.")






# class NovelRequests(NovelCrawler):
#     # def __init__(self, url, output_dir, sleep_time=1000):
#     #     self.url = url
#     #     self.base_url = extract_base_url(url)
#     #     # print("Base URL:", self.base_url)
#     #     self.output_dir = output_dir + "outputs/Novel/" if output_dir else "outputs/Novel/"
#     #     self.sleep_time = sleep_time / 1000  # Convert milliseconds to seconds

#     #     data = pd.read_csv("data/aliases.csv")
#     #     # print("Data:\n", data)
#     #     # print("\n\n")
#     #     # site_name = data.loc[data['site'] == self.base_url, 'name'].values
#     #     site = self.base_url.split("//")[-1].split("/")[0]
#     #     # print("Site:", site)
#     #     site_name = data[data['site'] == site]['name'].values[0]
#     #     # print("Site name:", site_name)
#     #     with open(f"data/formats/{site_name}.json", 'r', encoding='utf-8') as f:
#     #         self.format_data = json.load(f)


#     #     self.title = None
#     #     self.novel_info = {
#     #         "title": None,
#     #         "author": None,
#     #         "other_info": {},
#     #         "cover_image": None,
#     #         "num_chapters": None,
#     #         "description": None,
#     #         "genres": [],
#     #         "novel_url": self.url,
#     #         "start_chapter": None,
#     #         "end_chapter": None,
#     #         "chapter_links": [],
#     #         }
        
#     #     if not os.path.exists(self.output_dir):
#     #         os.makedirs(self.output_dir)

#     def get_all_info(self, custom_volume_list=None, info_url=None):
#         # custom_volume_list: list of volume links to crawl instead of extracting from page
#         if custom_volume_list and info_url:
#             self.url = info_url
#         html_content = get_page_content(self.url)
#         print("Fetching novel information from:", self.url)
#         print("Page content fetched.")
#         # print(BeautifulSoup(html_content, 'html.parser').prettify())
#         if not html_content:
#             print("Failed to retrieve page content.")
#             return
        


#         # get novel title
#         title_args = {k: v for k, v in self.format_data['title'].items() if v!=""}
#         self.title = get_title(html_content, 
#                                **title_args
#                                )
#         self.output_dir = os.path.join(self.output_dir, self.title)
#         if not os.path.exists(self.output_dir):
#             os.makedirs(self.output_dir)
#         self.novel_info['title'] = self.title

#         # get cover image
#         cover_args = {k: v for k, v in self.format_data['cover'].items() if v!=""}
#         referrer = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
#         cover_image_url = get_cover_image_element(html_content,
#                                                   output_dir=self.output_dir+"/img", ext="jpg",
#                                                   img_name="cover", img_referrer=referrer, **cover_args)
#         self.novel_info['cover_image'] = cover_image_url

#         # get genres
#         genre_args = {k: v for k, v in self.format_data['genre'].items() if v!=""}
#         genres = get_genres(None, html_content, **genre_args)
#         self.novel_info['genres'] = genres


#         holders = {k: v for k, v in self.format_data['other_info']['holder'].items() if v!=""}
#         values = {k: v for k, v in self.format_data['other_info']['value'].items() if v!=""}
#         other_info = get_all_other_novel_info(html_content, holders=holders, values=values)
#         for key, value in other_info.items():
#             if key.lower() == 'tác giả' or key.lower() == 'author':
#                 self.novel_info['author'] = value
#             # can add more info extraction here if needed
#             else:
#                 self.novel_info["other_info"][key] = value
#                 # self.novel_info[key] = value

        
#         description_args = {k: v for k, v in self.format_data['description'].items() if v!=""}
#         description = get_description(None, html_content, **description_args)
#         self.novel_info['description'] = description

#         if custom_volume_list:
#             volume_links = custom_volume_list
            
#             vol_page = self.format_data['vol_page']
#             vol_title = {k: v for k, v in vol_page['vol_title'].items() if v!=""}
#             if 'vol_cover' in vol_page:
#                 vol_cover = {k: v for k, v in vol_page['vol_cover'].items() if v!=""}
#                 vol_cover['output_dir'] = self.output_dir+"/img"
#                 vol_cover['img_referrer'] = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
#             else:
#                 vol_cover = {}
#             vol_chap = {k: v for k, v in vol_page['chapter_list'].items() if v!=""}
#             volumes = []
#             for i, link in enumerate(volume_links):
#                 if vol_cover:
#                     vol_cover['img_name'] = f"vol_{i+1}_cover"
#                 vol_info = get_volume_info_from_link(base_url=self.base_url, vol_url=link, 
#                                                     title_args=vol_title,
#                                                     cover_args=vol_cover,
#                                                     chap_list_args=vol_chap)
#                 volumes.append(vol_info)
#                 time.sleep(1)  # sleep between volume requests
#         else:
#             group = self.format_data['vol_group']
#             vol_section = {k: v for k, v in group['vol_section'].items() if v!=""}
#             vol_title = {k: v for k, v in group['vol_title'].items() if v!=""}
#             vol_cover = {k: v for k, v in group['vol_cover'].items() if v!=""}
#             vol_cover['output_dir'] = self.output_dir+"/img"
#             vol_chap = {k: v for k, v in group['chapter_list'].items() if v!=""}
#             volumes = get_all_volume(html_content, vol_sect=vol_section,
#                                     vol_title=vol_title,
#                                     vol_cover=vol_cover,
#                                     vol_chap=vol_chap,
#                                   base_url=self.base_url)

       
#         print("Got all novel information.")
#         with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
#             json.dump({"info" : self.novel_info, "volumes": volumes}, f, ensure_ascii=False, indent=4)
#         return self.novel_info, volumes
        

#     def crawl_chapter_(self, chapter_url, img_output_dir=None, img_prefix=""):
#         try:
#             chapter_content = get_page_content(chapter_url)
#         except Exception as e:
#             print(f"Error fetching chapter content from {chapter_url}: {e}")
#             print("Retrying...")
#             for _attempt in range(5):
#                 time.sleep(2)  # wait before retrying
#                 chapter_content = get_page_content(chapter_url)
#                 if chapter_content:
#                     break
#             else:
#                 print(f"Failed to retrieve chapter content from {chapter_url} after retries.")
#                 return None
#             chapter_content = get_page_content(chapter_url)
#         if not chapter_content:
#             print(f"Failed to retrieve chapter content from {chapter_url}")
#             return None
#         chapter_prop = self.format_data['chapter']
#         try:
#             chapter_title = get_chapter_title(chapter_content, chapter_prop['title'], update_log=self.update_log)
#         except Exception as e:
#             print(f"Error extracting chapter title from {chapter_url}: {e}")
#             print("Retrying...")
#             for _attempt in range(5):
#                 time.sleep(2)  # wait before retrying
#                 chapter_content = get_page_content(chapter_url)
#                 chapter_title = get_chapter_title(chapter_content, chapter_prop['title'], update_log=self.update_log)
#                 if chapter_title:
#                     break
#             else:
#                 print(f"Failed to extract chapter title from {chapter_url} after retries.")
#                 chapter_title = "No Title"
#         chapter_body = get_chapter_content(chapter_content, **chapter_prop['content'])
#         soup = BeautifulSoup(chapter_body, 'html.parser')
       
#         img_args = {k: v for k, v in chapter_prop['image'].items() if k not in ['delete'] and v!=""}
#         img_src = get_image_urls(chapter_body, self.base_url, **img_args)
#         imgs = soup.find_all(**{k: v for k, v in img_args.items() if k not in ['other_attr'] and v!=""})
#         # print("Images found: ", len(img_src))
#         # print("Pre download Images URLs: ", imgs)
#         if chapter_prop['image']['delete']:
#             for i in range(abs(chapter_prop['image']['delete'])):
#                 # img_ele[-1].decompose()  # remove unwanted img tags (ads)
#                 # print(i)
#                 imgs[-(i+1)].decompose()
#                 img_src.pop(-1)
#                 # print("Images left after deletion: ", len(img_src))
#         # print("Images to download: ", len(img_src))
#         # print("Image elements: ", imgs)
#         # imgs_ = soup.find_all(**{k: v for k, v in img_args.items() if k not in ['other_attr'] and v!=""})
#         # print("Images after deletion: ", len(imgs_))
#         chapter_body = soup.prettify()
        
#         chapter_img_folder = None
#         for i, img_url in enumerate(img_src):
#             img_name = f"{img_prefix}_img{i+1}"
#             local_img_path = download_image(img_url, output_dir=img_output_dir, ext="jpg", name=img_name,
#                                         img_referrer=self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False)
#             new_chapter_body = re.sub(rf'{img_url}', local_img_path, chapter_body)
        
#             chapter_body = new_chapter_body
        
#             chapter_img_folder = os.path.dirname(local_img_path)
        
#         # else:
#         #     chapter_img_folder = None
        
#         return {
#             "chapter_title": chapter_title,
#             "chapter_content": chapter_body,
#             "chapter_img_folder": chapter_img_folder
#         }

    

#     def crawl(self, custom_volume_list=None, info_url=None):
#         novel_info, volumes = self.get_all_info(custom_volume_list=custom_volume_list, info_url=info_url)
#         for idx, vol in enumerate(volumes):
#             all_chapters = []
#             print(f"Crawling Volume {idx+1}: {vol['title']}")
           
#             for jdx, chap_url in tqdm.tqdm(enumerate(vol['chapter_links']),
#                                            total=len(vol['chapter_links']),
#                                            desc=f"Crawling Volume {idx+1} Chapters", unit="chapter"):
#                 # print(f"Crawling Volume {idx+1} - Chapter {jdx+1}: {chap_url}")
#                 chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
#                                                   img_prefix=f"vol{idx+1}_chap{jdx+1}")
#                 all_chapters.append(chapter_data) if chapter_data else {}
#                 time.sleep(self.sleep_time)
#             vol['chapter_contents'] = all_chapters

#         info = {"info" : novel_info}
#         all_vol = {"volumes": volumes}

#         with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
#             json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
#         print("Crawling completed and data saved.")


#     def crawl_range(self, start_chapter, end_chapter, custom_volume_list=None, info_url=None):
#         novel_info, volumes = self.get_all_info(custom_volume_list=custom_volume_list, info_url=info_url)
#         flattened_chapter_links = []
#         chapter_map = []  # To keep track of which chapter belongs to which volume
#         for vol_idx, vol in enumerate(volumes):
#             for chap_url in vol['chapter_links']:
#                 flattened_chapter_links.append(chap_url)
#                 chapter_map.append(vol_idx)
#         selected_chapter_links = flattened_chapter_links[start_chapter-1:end_chapter-1]
#         all_chapters = []
#         for i in tqdm.tqdm(range(len(selected_chapter_links)), desc="Crawling Selected Chapters", unit="chapters"):
#             chap_url = selected_chapter_links[i]
#             vol_idx = chapter_map[start_chapter - 1 + i]
#             chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
#                                               img_prefix=f"vol{vol_idx+1}_chap{start_chapter + i}")
#             all_chapters.append(chapter_data) if chapter_data else {}
#             time.sleep(self.sleep_time)

#         vol_0 = {
#             "title": f"Chapters {start_chapter} to {end_chapter}",
#             "cover_image": None,
#             "chapter_links": selected_chapter_links,
#             "chapter_contents": all_chapters
#         }
#         info = {"info" : novel_info}
#         all_vol = {"volumes": [vol_0]}
#         with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
#             json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
#         print("Crawling of selected chapter range completed and data saved.")


#     def crawl_chapter(self, chapter_url, info_url=None):
#         if info_url:
#             self.url = info_url
#         novel_info, volumes = self.get_all_info(info_url=self.url)
#         chapter_data = self.crawl_chapter_(chapter_url, img_output_dir=self.output_dir+"/img", 
#                                           img_prefix="single_chap")
#         vol_0 = {
#             "title": "Single Chapter Crawl",
#             "cover_image": None,
#             "chapter_links": [chapter_url],
#             "chapter_contents": [chapter_data] if chapter_data else []
#         }
#         info = {"info" : novel_info}
#         all_vol = {"volumes": [vol_0]}
#         with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
#             json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
#         print("Crawling of single chapter completed and data saved.")


#     def make_epub(self):
#         # self.output_dir = os.path.join(self.output_dir, "Hội chứng muốn sống bình an tại dị giới")
#         novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
#         if not os.path.exists(novel_info_path):
#             print("Novel information file not found. Please run the crawler first.")
#             return
#         with open(novel_info_path, 'r', encoding='utf-8') as f:
#             data = json.load(f)
#         novel_info = data['info']
#         volumes = data['volumes']

#         # make volumes epub elements
#         volume_list = []
#         for idx, vol in enumerate(volumes):
#             print(f"Preparing Volume: {vol['title']}")
#             chapter_elements = []
#             for chap_idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
#                                      desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
#                 c = make_chapter_epub(chapter, title_added=(f"Vol {idx+1}", ""), idx=chap_idx+1)
#                 chapter_elements.append(c)
#             v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
#             volume_list.append((v_img, v, c_lst))
#         # create epub
#         make_book_epub(novel_info, volume_list, ("",""), output_path=self.output_dir, ebook_name=novel_info['title'] + ".epub")



#     def make_volume_epub(self):
#         novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
#         if not os.path.exists(novel_info_path):
#             print("Novel information file not found. Please run the crawler first.")
#             return
#         with open(novel_info_path, 'r', encoding='utf-8') as f:
#             data = json.load(f)
#         novel_info = data['info']
#         volumes = data['volumes']

#         for vol in volumes:
#             print(f"Preparing Volume: {vol['title']}")
#             chapter_elements = []
#             for chap_idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
#                                      desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
#                 c = make_chapter_epub(chapter, title_added=("", ""), idx=chap_idx+1)
#                 chapter_elements.append(c)
#             v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
#             # create epub for each volume
#             make_book_epub(novel_info, [(v_img, v, c_lst)], ("",""), output_path=self.output_dir, ebook_name=novel_info['title'] + f" - {vol['title']}.epub")


# class NovelSelenium(NovelCrawler):
#     def __init__(self, url, output_dir, sleep_time=1000):
#         super().__init__(url, output_dir, sleep_time)
#         self.driver = webdriver.Chrome()  # Ensure you have the ChromeDriver installed and in PATH
#         # self.driver = uc.Chrome()  # Using undetected-chromedriver
#         # print("Initialized Selenium WebDriver for url.")
#         # print(f"URL: {self.url}")
#         # print(f"Site name: {self.site_name}")

#     def get_page_content(self, url):
#         # print("getting page content...")
#         self.driver.implicitly_wait(100)  # Wait up to 10 seconds for elements to load
#         html = ""
#         try:
#             self.driver.get(url)
#             self.driver.find_element(By.CLASS_NAME, self.format_data["cover"]["class_"])  # Ensure the body tag is loaded
#             html = self.driver.page_source
#         except Exception as e:

#             # with SB(uc=True, xvfb=True) as sb:
#             #     # 1. Truy cập trang web với cơ chế tự động kết nối lại nếu bị chặn ban đầu
#             #     sb.uc_open_with_reconnect(url, reconnect_time=4)
                
#             #     # 2. Xử lý xác minh "Verify you are human" (nếu có)
#             #     # Phương thức này mô phỏng click chuột thực tế để tránh bị phát hiện
#             #     sb.uc_gui_click_captcha()
#             #     sb.wait_for_ready_state()
#             #     # 3. Đợi một chút để trang tải hoàn tất sau khi xác minh
#             #     sb.sleep(2)
#             bypass_capcha(self.driver, url)
#             html = self.driver.page_source

#                 # html = sb.driver.page_source
#             # html = bypass_capcha_source(url)
#             print("Error loading page:", e)
#         soup = BeautifulSoup(html, 'html.parser')
#         html_content = soup.prettify()
#         print("Page content fetched.")
#         # with open("test.html", "w", encoding="utf-8") as f:
#         #     f.write(html_content)
#         # print("Page content: get")
#         return html_content

#     def check_login_btn(self, html_content):
#         soup = BeautifulSoup(html_content, 'html.parser')
#         # is_log_in_btn = soup.find(class_=self.format_data['login']['login_btn']['class_']) is None
#         # print("Checking login button...")
#         try:
#             is_log_in_btn = soup.find(class_=self.format_data['login']['login_btn']['class_']) is not None
#         except Exception as e:
#             is_log_in_btn = False
#             # print("Error checking login button:", e)
#         return is_log_in_btn
    
#     def login_to_site(self, site_name, html_content):
#         login_btn = self.format_data['login']['login_btn']
#         user_field = self.format_data['login']['username']
#         password_field = self.format_data['login']['password']
#         submit_btn = self.format_data['login']['submit_btn']
#         logged_in = login(html_content, self.driver, log_btn_args=login_btn, user_field_args=user_field, 
#                        pass_field_args=password_field, submit_btn_args=submit_btn, credential_name=site_name,
#                        anchor_class=self.format_data["vol_group"]["vol_section"]["class_"])
#         # page_log_in = self.driver.page_source
#         self.site_name = site_name
#         # with open("test_login.html", "w", encoding="utf-8") as f:
#         #     f.write(page_log_in)
#         return logged_in

#     def get_all_info(self, custom_volume_list=None, info_url=None):
#         self.get_page_content(self.url) if not info_url else self.get_page_content(info_url)
#         if self.format_data.get('login', None):
#             self.login_to_site(self.site_name, self.driver.page_source)
#         soup = BeautifulSoup(self.driver.page_source, 'html.parser')
#         html_content = soup.prettify()
#         # print("Fetching novel information from:", self.url)
#         # print("Page content fetched.")
#         if not html_content:
#             print("Failed to retrieve page content.")
#             return

#         # get novel title
#         title_args = {k: v for k, v in self.format_data['title'].items() if v!=""}
#         # print("Title args:", title_args)
#         self.title = get_title(html_content, 
#                                **title_args
#                                )
#         self.output_dir = os.path.join(self.output_dir, self.title)
#         if not os.path.exists(self.output_dir):
#             os.makedirs(self.output_dir)
#         self.novel_info['title'] = self.title

#         # get cover image
#         cover_args = {k: v for k, v in self.format_data['cover'].items() if v!=""}
#         referrer = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
#         cover_image_url = get_cover_image_element(html_content,
#                                                   output_dir=self.output_dir+"/img", ext="jpg",
#                                                   img_name="cover", img_referrer=referrer, **cover_args)
#         self.novel_info['cover_image'] = cover_image_url

#         # get genres
#         genre_args = {k: v for k, v in self.format_data['genre'].items() if v!=""}
#         genres = get_genres(self.driver, html_content, **genre_args)
#         self.novel_info['genres'] = genres

#         holders = {k: v for k, v in self.format_data['other_info']['holder'].items() if v!=""}
#         values = {k: v for k, v in self.format_data['other_info']['value'].items() if v!=""}
#         other_info = get_all_other_novel_info(html_content, holders=holders, values=values)
#         for key, value in other_info.items():
#             if key.lower() == 'tác giả' or key.lower() == 'author':
#                 self.novel_info['author'] = value
#             # can add more info extraction here if needed
#             else:
#                 self.novel_info["other_info"][key] = value
#                 # self.novel_info[key] = value

#         description_args = {k: v for k, v in self.format_data['description'].items() if v!=""}
#         description = get_description(self.driver, html_content, **description_args)
#         self.novel_info['description'] = description

#         html_content = self.driver.page_source

#         group = self.format_data['vol_group']
#         vol_section = {k: v for k, v in group['vol_section'].items() if v!=""}
#         vol_title = {k: v for k, v in group['vol_title'].items() if v!=""}
#         vol_cover = {k: v for k, v in group['vol_cover'].items() if v!=""}
#         vol_cover['output_dir'] = self.output_dir+"/img"
#         vol_chap = {k: v for k, v in group['chapter_list'].items() if v!=""}
#         volumes = get_all_volume(html_content, vol_sect=vol_section,
#                                 vol_title=vol_title,
#                                 vol_cover=vol_cover,
#                                 vol_chap=vol_chap,
#                               base_url=self.base_url)

#         print("Got all novel information.")
#         with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
#             json.dump({"info" : self.novel_info, "volumes": volumes}, f, ensure_ascii=False, indent=4)
#         return self.novel_info, volumes

#     def crawl_chapter_(self, chapter_url, img_output_dir=None, img_prefix=""):
#         try:
#             self.driver.get(chapter_url)
#             self.driver.implicitly_wait(10)  # Wait up to 10 seconds for elements to load
#             logged_in = True
#             if self.format_data.get('login', None):
#                 logged_in = self.check_login_btn(self.driver.page_source)
#             if not logged_in:
#                 self.login_to_site(self.site_name, self.driver.page_source)
#             chapter_content = self.driver.page_source
#         except Exception as e:
#             print(f"Error fetching chapter content from {chapter_url}: {e}")
#             print("Retrying...")
#             for _attempt in range(5):
#                 self.driver.implicitly_wait(10)  # Wait up to 10 seconds for elements to load
#                 self.driver.get(chapter_url)
#                 logged_in = self.check_login_btn(self.driver.page_source)
#                 if not logged_in:
#                     self.login_to_site(self.site_name, self.driver.page_source)
#                 chapter_content = self.driver.page_source
#                 if chapter_content:
#                     break
#             else:
#                 print(f"Failed to retrieve chapter content from {chapter_url} after retries.")
#                 return None
#             chapter_content = self.driver.page_source
#         if not chapter_content:
#             print(f"Failed to retrieve chapter content from {chapter_url}")
#             return None
#         chapter_prop = self.format_data['chapter']
#         chapter_title = get_chapter_title(chapter_content, **chapter_prop['title'])
#         chapter_body = get_chapter_content(chapter_content, **chapter_prop['content'])
#         soup = BeautifulSoup(chapter_body, 'html.parser')
       
#         img_args = {k: v for k, v in chapter_prop['image'].items() if k not in ['delete'] and v!=""}
#         img_src = get_image_urls(chapter_body, self.base_url, **img_args)
#         imgs = soup.find_all(**{k: v for k, v in img_args.items() if k not in ['other_attr'] and v!=""})
#         # print("Images found: ", len(img_src))
#         # print("Pre download Images URLs: ", imgs)
#         if "delete" in chapter_prop['image'] and chapter_prop['image']['delete']:
#             for i in range(abs(chapter_prop['image']['delete'])):
#                 # img_ele[-1].decompose()  # remove unwanted img tags (ads)
#                 # print(i)
#                 imgs[-i-1].decompose()
#                 img_src.pop(-1)
#                 # print("Images left after deletion: ", len(img_src))
#         chapter_body = soup.prettify()
        
#         chapter_img_folder = None
#         for i, img_url in enumerate(img_src):
#             img_name = f"{img_prefix}_img{i+1}"
#             local_img_path = download_image(img_url, output_dir=img_output_dir, ext="jpg", name=img_name,
#                                         img_referrer=self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False)
#             new_chapter_body = re.sub(rf'{img_url}', local_img_path, chapter_body)
        
#             chapter_body = new_chapter_body
        
#             chapter_img_folder = os.path.dirname(local_img_path)
        
#         # else:
#         #     chapter_img_folder = None
        
#         return {
#             "chapter_title": chapter_title,
#             "chapter_content": chapter_body,
#             "chapter_img_folder": chapter_img_folder
#         }

#     def crawl(self, custom_volume_list=None, info_url=None, keep_logged_in=True):
#         novel_info, volumes = self.get_all_info()
#         for idx, vol in enumerate(volumes):
#             # if idx != 12:
#             #     continue
#             all_chapters = []
#             print(f"Crawling Volume {idx+1}: {vol['title']}")
           
#             for jdx, chap_url in tqdm.tqdm(enumerate(vol['chapter_links']),
#                                            total=len(vol['chapter_links']),
#                                            desc=f"Crawling Volume {idx+1} Chapters", unit="chapter"):
#                 # print(f"Crawling Volume {idx+1} - Chapter {jdx+1}: {chap_url}")
#                 chapter_data = None
#                 try:
#                     chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
#                                                     img_prefix=f"vol{idx+1}_chap{jdx+1}", keep_logged_in=keep_logged_in)
#                 except Exception as e:
#                     print(f"Error crawling chapter {chap_url}: {e}")
#                     print("Retrying...")
#                     for _attempt in range(5):
#                         time.sleep(2)  # wait before retrying
#                         try:
#                             chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
#                                                             img_prefix=f"vol{idx+1}_chap{jdx+1}", 
#                                                             keep_logged_in=keep_logged_in)
#                             if chapter_data:
#                                 break
#                         except Exception as e:
#                             print(f"Retry {_attempt+1} failed: {e}")
#                         except KeyboardInterrupt:
#                             print("Crawling interrupted by user.")
#                             with open("debug_chapter.html", "w", encoding="utf-8") as f:
#                                 f.write(BeautifulSoup(self.driver.page_source, "html.parser").prettify())
#                             print("Page source for debugging stored")
#                             return
#                     else:
#                         print(f"Failed to crawl chapter {chap_url} after retries.")
#                         print("Pausing for 60 seconds...")
#                         print("Page source for debugging:")
#                         print(BeautifulSoup(self.driver.page_source, "html.parser").prettify())
#                         with open("debug_chapter.html", "w", encoding="utf-8") as f:
#                             f.write(BeautifulSoup(self.driver.page_source, "html.parser").prettify())
#                 all_chapters.append(chapter_data) if chapter_data else {}
#                 time.sleep(self.sleep_time)
#             vol['chapter_contents'] = all_chapters

#         info = {"info" : novel_info}
#         all_vol = {"volumes": volumes}

#         with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
#             json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
#         print("Crawling completed and data saved.")
    
#     def crawl_range(self, start_chapter, end_chapter, custom_volume_list=None, info_url=None, keep_logged_in=True):
#         novel_info, volumes = self.get_all_info()
#         flattened_chapter_links = []
#         chapter_map = []  # To keep track of which chapter belongs to which volume
#         for vol_idx, vol in enumerate(volumes):
#             for chap_url in vol['chapter_links']:
#                 flattened_chapter_links.append(chap_url)
#                 chapter_map.append(vol_idx)
#         selected_chapter_links = flattened_chapter_links[start_chapter-1:end_chapter-1]
#         all_chapters = []
#         for i in tqdm.tqdm(range(len(selected_chapter_links)), desc="Crawling Selected Chapters", unit="chapters"):
#             chap_url = selected_chapter_links[i]
#             vol_idx = chapter_map[start_chapter - 1 + i]
#             chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
#                                               img_prefix=f"vol{vol_idx+1}_chap{start_chapter + i}", 
#                                               keep_logged_in=keep_logged_in)
#             all_chapters.append(chapter_data) if chapter_data else {}
#             time.sleep(self.sleep_time)

#         vol_0 = {
#             "title": f"Chapters {start_chapter} to {end_chapter}",
#             "cover_image": None,
#             "chapter_links": selected_chapter_links,
#             "chapter_contents": all_chapters
#         }
#         info = {"info" : novel_info}
#         all_vol = {"volumes": [vol_0]}
#         with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
#             json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
#         print("Crawling of selected chapter range completed and data saved.")
    
#     def crawl_chapter(self, chapter_url, info_url=None, keep_logged_in=True):
#         novel_info, volumes = self.get_all_info(info_url=info_url) if info_url else self.get_all_info()
#         chapter_data = self.crawl_chapter_(chapter_url, img_output_dir=self.output_dir+"/img", 
#                                           img_prefix="single_chap", keep_logged_in=keep_logged_in)
#         vol_0 = {
#             "title": "Single Chapter Crawl",
#             "cover_image": None,
#             "chapter_links": [chapter_url],
#             "chapter_contents": [chapter_data] if chapter_data else []
#         }
#         info = {"info" : novel_info}
#         all_vol = {"volumes": [vol_0]}
#         with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
#             json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
#         print("Crawling of single chapter completed and data saved.")
    
#     def make_epub(self):
#         # self.output_dir = os.path.join(self.output_dir, "Hội chứng muốn sống bình an tại dị giới")
#         novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
#         if not os.path.exists(novel_info_path):
#             print("Novel information file not found. Please run the crawler first.")
#             return
#         with open(novel_info_path, 'r', encoding='utf-8') as f:
#             data = json.load(f)
#         novel_info = data['info']
#         volumes = data['volumes']

#         # make volumes epub elements
#         volume_list = []
#         for vol in volumes:
#             print(f"Preparing Volume: {vol['title']}")
#             chapter_elements = []
#             for idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
#                                      desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
#                 c = make_chapter_epub(chapter, title_added=("", ""), idx=idx+1)
#                 chapter_elements.append(c)
#             v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
#             volume_list.append((v_img, v, c_lst))
#         # create epub
#         make_book_epub(novel_info, volume_list, ("",""), output_path=self.output_dir, ebook_name=novel_info['title'] + ".epub")

    
#     def make_volume_epub(self):
#         novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
#         if not os.path.exists(novel_info_path):
#             print("Novel information file not found. Please run the crawler first.")
#             return
#         with open(novel_info_path, 'r', encoding='utf-8') as f:
#             data = json.load(f)
#         novel_info = data['info']
#         volumes = data['volumes']

#         for vol in volumes:
#             print(f"Preparing Volume: {vol['title']}")
#             chapter_elements = []
#             for idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
#                                      desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
#                 c = make_chapter_epub(chapter, title_added=("", ""), idx=idx+1)
#                 chapter_elements.append(c)
#             v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
#             # create epub for each volume
#             make_book_epub(novel_info, [(v_img, v, c_lst)], ("",""), output_path=self.output_dir, ebook_name=novel_info['title'] + f" - {vol['title']}.epub")
        
    
        
        


def test():
    url1 = "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun"
    url = "https://valvrareteam.net/truyen/no-game-no-life-cd23c8d9"

    vol_lst = ["https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t17045-arc-2-vach-tran"]
    vol_lst1 = [
        "https://docln.sbs/truyen/8306-isekai-demo-bunan-ni-ikitai-shoukougun/t12965-arc-1",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t17045-arc-2-vach-tran",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t18598-arc-3-tao-ngo",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t19121-arc-4-ke-sach",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t20030-arc-5-khai-mac",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t20803-arc-6-kich-chien",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t21609-arc-7-lua-chon",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t22375-arc-8-cham-dut",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t23615-arc-9-binh-an",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t21555-ngoai-truyen"
    ]



    crawler = NovelSelenium(url, output_dir=None, sleep_time=1000)
    page_content = crawler.get_page_content(url)
    print("output dir:", crawler.output_dir)
    logged_in = crawler.login_to_site("valvrareteam", page_content)
    print("Logged in:", logged_in)
    # crawler.get_all_info()
    crawler.crawl()
    # crawler.run()

    # crawler = NovelCrawler(url, output_dir=None, sleep_time=1000)
    # # crawler.get_all_info()
    # crawler.crawl()
    # # crawler.crawl_chapter(chap2)
    # print("output dir:", crawler.output_dir)
    # crawler.make_epub()


def run(**kwargs):
    # print("args:", kwargs)
    requested_max_workers = kwargs.get("max_workers", None)
    max_workers = (
        DEFAULT_MAX_WORKERS
        if requested_max_workers is None
        else validate_max_workers(requested_max_workers)
    )
    db = pd.read_csv("data/aliases.csv")
    print(db)
    url = kwargs.get("novel_url", "")
    url = f"https://{url}" if url and not str(url).startswith(("http://", "https://")) else url
    print("URL:", url)
    output_dir = kwargs.get("output_dir", None)
    sleep_time = kwargs.get("sleep_time", 1000)
    crawl_type = kwargs.get("crawl_type", "full")  # full, range, single
    # custom_volume_list = kwargs.get("custom_volume_list", None) # list of volume URLs
    crawl_type_args = kwargs.get("crawl_type_args", {})  # dict with args for crawl type
    if not isinstance(crawl_type_args, dict):
        raise ValueError("crawl_type_args must be a dictionary")
    book_type = kwargs.get("book_type", "all")  #  all, volume
    keep_logged_in = kwargs.get("keep_logged_in", False)  # whether to keep logged in state during crawling (for Selenium-based crawler)
    fetch_mode = kwargs.get("fetch_mode", None)  # requests / browser / auto — overrides site format default
    headless = kwargs.get("headless", None)  # None → resolved by crawler (default True)
    if not url:
        print("No URL provided.")
        return
    print("Starting crawler for URL:", url)
    base_url = extract_base_url(url)
    print("Base URL:", base_url)
    base_url_ = base_url.split("//")[-1].replace("www.", "")
    normalized_sites = db["site"].astype(str).str.strip().str.replace("www.", "", regex=False).str.rstrip("/")
    site_idx = db.index[normalized_sites == base_url_.rstrip("/")]
    if site_idx.empty:
        raise ValueError(f"Site '{base_url_}' was not found in data/aliases.csv.")
    site_name = db.at[site_idx[0], 'name']
    class_name = db.at[site_idx[0], 'crawler_class']
    crawler = None
    custom_volume_list = parse_custom_volume_list(
        crawl_type_args.get("custom_volume_list", None)
    )
    info_url = crawl_type_args.get("info_url", None)
    start_chap = crawl_type_args.get("start_chapter", None)
    end_chap = crawl_type_args.get("end_chapter", None)
    chap_url = crawl_type_args.get("chapter_url", None)

    # print(f"URL: {url}\noutput_dir: {output_dir}\nsleep_time: {sleep_time}\ncrawl_type: {crawl_type}\ncrawl_type_args: {crawl_type_args}\nbook_type: {book_type}\nkeep_logged_in: {keep_logged_in}")


    if class_name == "DoclnCrawler":
        print("Using authenticated Docln crawler.")
        if fetch_mode:
            print(f"  (Note: DoclnCrawler uses its own authenticated session; fetch_mode '{fetch_mode}' is ignored)")
        from crawler.crawl_docln import DoclnCrawler
        crawler = DoclnCrawler(url, output_dir=output_dir, sleep_time=sleep_time, max_workers=max_workers)
    elif class_name == "XCrawler":
        print("Using generated chapter URL crawler.")
        from crawler.X import XCrawler
        crawler = XCrawler(
            url,
            output_dir=output_dir,
            sleep_time=sleep_time,
            start_chapter=start_chap,
            end_chapter=end_chap,
            keep_logged_in=keep_logged_in,
            driver=False,
            fetch_mode=fetch_mode,
            headless=headless,
            max_workers=max_workers,
        )
    elif class_name == "NovelSelenium":
        print("Using Selenium-based crawler.")
        # crawler = NovelSelenium(url, output_dir=output_dir, sleep_time=sleep_time)
        # custom_volume_list = None
        crawler = NovelCrawler(url, output_dir=output_dir, sleep_time=sleep_time, keep_logged_in=keep_logged_in, driver=True, fetch_mode=fetch_mode, headless=headless, max_workers=max_workers)
        # info_url = None
    else:
        print("Using Requests-based crawler.")
        # crawler = NovelRequests(url, output_dir=output_dir, sleep_time=sleep_time)
        crawler = NovelCrawler(url, output_dir=output_dir, sleep_time=sleep_time, keep_logged_in=False, driver=False, fetch_mode=fetch_mode, headless=headless, max_workers=max_workers)
    # print("output dir:", crawler.output_dir)

    if class_name == "XCrawler":
        if start_chap is None or end_chap is None:
            raise ValueError("XCrawler requires start_chapter and end_chapter.")
        crawl_type = "range"


    # crawler.get_page_content(url)

    try:
        if crawl_type == "full":
            if custom_volume_list:
                crawler.crawl(custom_volume_list=custom_volume_list, info_url=info_url)
            else:
                crawler.crawl()
        elif crawl_type == "range":
            start_chap = crawl_type_args.get("start_chapter", 1)
            end_chap = crawl_type_args.get("end_chapter", 1)
            
            crawler.crawl_range(start_chap, end_chap, custom_volume_list=custom_volume_list, info_url=info_url)
        elif crawl_type == "single":
            chap_url = crawl_type_args.get("chapter_url", "")
            if not chap_url:
                print("No chapter URL provided for single chapter crawl.")
                return
            crawler.crawl_chapter(chap_url, info_url=info_url)
        
        if book_type == "all":
            crawler.make_epub()
        elif book_type == "volume":
            crawler.make_volume_epub()
    finally:
        if crawler:
            crawler.close()

    return True

def make_epub():
    novel_info_path = input("Enter the path to novel_info.json file: ").strip()
    make_volume = input("Make separate EPUB for each volume? (y/n, default n): ").strip().lower() == "y"
    if not os.path.exists(novel_info_path):
        print("Novel information file not found. Please run the crawler first.")
        return
    crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)  # Dummy crawler instance to use make_epub method
    if make_volume:
        crawler.make_volume_epub(novel_info_path=novel_info_path)
    else:
        crawler.make_epub(novel_info_path=novel_info_path)


def novel_crawl():
    novel_url = input("Enter the novel URL: ").strip()
    normalized_novel_url = f"https://{novel_url}" if novel_url and not novel_url.startswith(("http://", "https://")) else novel_url
    output_dir = input("Enter the output directory (leave blank for default): ").strip()
    sleep_time = int(input("Enter sleep time between requests in milliseconds (default 1000): ").strip() or "1000")

    db = pd.read_csv("data/aliases.csv")
    base_url = extract_base_url(normalized_novel_url)
    base_url_ = base_url.split("//")[-1].replace("www.", "")
    normalized_sites = db["site"].astype(str).str.strip().str.replace("www.", "", regex=False).str.rstrip("/")
    site_idx = db.index[normalized_sites == base_url_.rstrip("/")]
    class_name = db.at[site_idx[0], "crawler_class"] if not site_idx.empty else None

    if class_name == "XCrawler":
        print("XCrawler site detected. Please provide the chapter range to crawl.")
        crawl_type = "range"
    else:
        crawl_type = input("Enter crawl type (full/range/single, default full): ").strip() or "full"
    custom_volume_list = None
    custom_volume_list_ = None
    if class_name != "XCrawler":
        custom_volume_list_ = input("Enter custom volume list (comma-separated URLs, leave blank for none, only for NovelRequest type): ").strip() or None
    if custom_volume_list_:
        custom_volume_list = [url.strip() for url in custom_volume_list_.split(",")]
    crawl_type_args = {"custom_volume_list": custom_volume_list} if custom_volume_list_ else {}
    novel_info_url = input("Enter novel info URL (leave blank for none): ").strip() or None if custom_volume_list_ else None
    if novel_info_url:
        crawl_type_args["info_url"] = novel_info_url
    if crawl_type == "range":
        start_chapter = int(input("Enter start chapter number: ").strip())
        end_chapter = int(input("Enter end chapter number: ").strip())
        crawl_type_args["start_chapter"] = start_chapter
        crawl_type_args["end_chapter"] = end_chapter
    elif crawl_type == "single":
        chapter_url = input("Enter the chapter URL: ").strip()
        crawl_type_args["chapter_url"] = chapter_url
    book_type = input("Enter book type (all/volume, default all): ").strip() or "all"
    keep_logged_in = input("Keep logged in during crawling? (y/n, default n): ").strip().lower() == "y"
    fetch_mode = input("Enter fetch mode (requests/browser/auto, leave blank for site default): ").strip() or None
    headless_input = input("Show browser window? (y/n, default n): ").strip().lower()
    headless = headless_input != "y"
    max_workers_input = input("Enter chapter workers (default 4): ").strip() or "4"
    max_workers = validate_max_workers(int(max_workers_input))

    run(novel_url=normalized_novel_url, output_dir=output_dir or None, sleep_time=sleep_time,
        crawl_type=crawl_type, crawl_type_args=crawl_type_args, book_type=book_type, keep_logged_in=keep_logged_in,
        fetch_mode=fetch_mode, headless=headless, max_workers=max_workers)

    
