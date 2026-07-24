import json
import os

import requests

from crawler.Novel import NovelCrawler
from test import get_login_token, get_page_with_cookies, login


class DoclnCrawler(NovelCrawler):
    """NovelCrawler variant that authenticates Docln requests with cookies."""

    _supports_parallel = False  # Uses a shared session for authenticated requests

    def __init__(self, url, output_dir=None, sleep_time=1000, secrets_path=None, max_workers=None):
        super().__init__(
            url=url,
            output_dir=output_dir,
            sleep_time=sleep_time,
            keep_logged_in=False,
            driver=False,
            max_workers=max_workers,
        )
        self.session = requests.Session()
        self.login_url = f"{self.base_url.rstrip('/')}/login"
        self.secrets_path = secrets_path or self._find_secrets_path()
        self.cookies = self._authenticate()

    @staticmethod
    def _find_secrets_path():
        for path in ("secrets.json", os.path.join("data", "secrets.json")):
            if os.path.exists(path):
                return path
        raise FileNotFoundError("Could not find secrets.json or data/secrets.json")

    def _authenticate(self):
        with open(self.secrets_path, "r", encoding="utf-8") as secrets_file:
            secrets = json.load(secrets_file)

        credentials = secrets.get("docln", secrets)
        try:
            username = credentials["username"]
            password = credentials["password"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"Docln credentials are missing from {self.secrets_path}"
            ) from exc

        token = get_login_token(self.login_url, session=self.session)
        if token is False:
            raise RuntimeError(f"Could not load Docln login page: {self.login_url}")

        cookies = login(
            url=self.login_url,
            username=username,
            password=password,
            token=token,
            session=self.session,
        )
        if not cookies:
            raise RuntimeError("Docln login failed")

        self.update_log("Authenticated with Docln using cookies.")
        return cookies

    def _get_page_content(self, url):
        content = get_page_with_cookies(
            url,
            self.cookies,
            session=self.session,
        )
        if content is False:
            raise RuntimeError(f"Could not load authenticated Docln page: {url}")
        return content


def docln_crawler_control(crawler, action, *args, compile_type="single_volume"):
    if action == "get_all":
        crawler.crawl(*args)
    elif action == "get_chapter_range":
        crawler.crawl_range(*args)
    elif action == "get_chapter":
        crawler.crawl_chapter(*args)
    else:
        raise ValueError(f"Unknown action: {action}")

    if compile_type == "single_volume":
        crawler.make_epub()
    elif compile_type == "multi_volume":
        crawler.make_volume_epub()
