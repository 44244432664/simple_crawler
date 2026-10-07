# Developer log

Project to download and make comic from 2 sites that lncrawler has not crawl yet: Truyenqq and Wikidich (also works for Wattpad at the current time since Wikidich is kind of copy Wattpad, just change the name and color). Yes, I know, I can just create a bot in lncrawl for Wikidich, but the thing is, I am stupid, so I have to do the hard way :>>>.
- To use the terminal interface, run this from the project root:

```bash
python main.py
```

The keyboard-first dashboard keeps the original commands: `comic`, `novel`,
`epub`, `multi`, `db`, and `exit`. It also accepts the displayed menu numbers
(and `batch` / `sources` as convenience aliases). The existing JSON batch
format remains supported, so previously created `to_crawl.json` files can be
used unchanged.

Yes, it is goes through a lot of if else, I will try to make it better if I still remember about it and/or have time in the future. Now I'm just gonna lie down before continue learning, I've neglected it for too long to feel like I'm doing sth useful :'>

\* If you need an emergency project to store the comics or novels that you love which might be gone in some near future and you do not have the money to buy the whole comic or the country just does not import the comic, hope you enjoy this project before finding a better one :3

<u>Note:</u> Will try to modify truyencv_downloader for better indexing (maybe), since native ereader(e.g. KoReader or NeoEreader) cannot seems to fully load the content of downloaded file.

# Vibe code content

## cloudflare bypass

There is no single bypass; it's a layered fallback pipeline built around SeleniumBase **UC Mode** (Undetected Chrome). Here's the mechanism end-to-end.

### 1. Fetch modes & per-site config

`PageFetcher` (utils/fetcher.py:232) has three modes: `requests`, `browser`, `auto` (utils/fetcher.py:188). Each site format JSON declares one — e.g. `x_truyenfull.json` and `foxaholic.json` use `auto`, others use `requests` (data/formats/*.json). The crawler resolves `fetch_mode` from CLI arg > site format > `"requests"` (crawler/Novel.py:92-99, 128) and builds both a main fetcher and a worker-fetcher factory (crawler/Novel.py:130-141).

### 2. Cheap `requests` path + challenge detection

`_requests_fetch()` (utils/fetcher.py:385) uses a plain `requests.Session` with a real Chrome UA. If the response is a Cloudflare challenge, `is_cloudflare_challenge()` (utils/fetcher.py:122) detects it via:
- Primary: the `cf-mitigated: challenge` response header (utils/fetcher.py:132)
- Fallback: strong HTML markers `challenge-platform`, `cf-chl-`, `cf-browser-verification`, `__cf_chl_opt`, `cf-error-details` (utils/fetcher.py:48-54)
- Weak markers (`"just a moment"`, `"attention required"`) only when status ≥ 400

When detected, it raises `CloudflareChallengeError` (utils/fetcher.py:422-423) instead of retrying (a 4xx challenge can't be solved by re-requesting).

### 3. Auto mode switches to a persistent browser

In `fetch()` (utils/fetcher.py:346-364) auto mode catches `CloudflareChallengeError`/`SelectorMismatchError`, logs "switching to browser", latches `self._browser_mode_active = True`, and delegates to `_browser_fetch()`. The latch is one-way: every later fetch in the run uses the browser, reusing the cleared session. In auto mode `_is_parallel_safe()` returns False (crawler/Novel.py:808), so chapters are crawled sequentially through this same fetcher — the cleared browser serves all pages.

### 4. The actual bypass — SeleniumBase UC Mode

`_ensure_browser()` (utils/fetcher.py:576) creates `Driver(uc=True, user_data_dir=profile, headless=...)`. The profile lives in `.crawler_profiles/<host>/` (e.g. `foxaholic_com/` exists on disk) so `cf_clearance`/`__cf_bm` cookies persist across runs. Then `uc_open_with_reconnect(url, reconnect_time=3)` (utils/fetcher.py:487) runs. Inside SeleniumBase this is:

- **Patched chromedriver binary** — `undetected/patcher.py` (`patch_exe`) rewrites the `window.cdc_<22chars>_…` string literals in the driver executable (whitespace/replaced via `gen_call_function_js_cache_name`), and `is_binary_patched` verifies the known signature `cdc_adoQpoasnfa76pfcZLmcfl_` is gone. This removes chromedriver's classic JS fingerprint.
- **Runtime global cleanup** — `remove_cdc_props_as_needed()` + `_get_cdc_props()` (undetected/__init__.py:363-399) walks the JS prototype chain for names matching `/^[a-z]{3}_[a-z]{22}_.*/i` and, via CDP `Page.addScriptToEvaluateOnNewDocument`, deletes them on every document — leaving `navigator.webdriver` undefined.
- **Driver detach during load** — `uc_open_with_reconnect` (browser_launcher.py:586) opens the URL in a new tab with `window.open`, then calls `driver.reconnect(reconnect_time)` (undetected/__init__.py:444) which **shuts down the chromedriver service** (`send_remote_shutdown_command`, `_terminate_process`), sleeps ~3s while the page's challenge JS runs with no automation endpoint attached, then restarts the service and recreates the session. This lets Cloudflare's challenge execute/auto-clear without detecting the driver.
- **Anti-detection launch flags** — `--disable-blink-features=AutomationControlled` and `excludeSwitches: ["enable-automation","enable-logging"]` (browser_launcher.py:4576-4581) stop Chrome itself from setting the automation flag.

### 5. Post-load verification & wait loop

`_browser_fetch()` then checks `_is_challenge_page(expected_selector)` (utils/fetcher.py:611) — if the expected content selector is present it's treated as cleared. If a challenge is still active it enters a poll loop up to `challenge_timeout` (utils/fetcher.py:499-562): headless waits for automatic verification; headed mode prints instructions for manual solving in the visible Chrome window. On success it returns `driver.page_source`; on timeout it dumps the page to `debug_pages/` and raises `ChallengeTimeoutError`. A final `wait_for_element(expected_selector)` (utils/fetcher.py:566) confirms real content, else `SelectorMismatchError`.

### 6. Cloudflare-protected image downloads

`download_image()` (utils/novel.py:316) first tries `requests` with a Referer header; on HTTP 403, `save_browser_image()` (utils/novel.py:387) runs an async `fetch(url, {credentials:'same-origin'})` inside the already-cleared UC Chrome via `execute_async_script` and base64-decodes the blob — fetching images from within the verified browser session.

### Key takeaways
- **Detection**: header + HTML markers in `is_cloudflare_challenge()`.
- **Bypass**: SeleniumBase UC mode = patched chromedriver + `cdc_` prop removal + kill/restart chromedriver mid-navigation (`uc_open_with_reconnect`) so the challenge clears while the driver is detached.
- **Persistence**: `user_data_dir` profiles keep `cf_clearance` cookies; auto mode's latch keeps all pages in the cleared browser; manual/automatic verification wait loops handle the rest.
