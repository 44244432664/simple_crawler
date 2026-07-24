import os
import time
import threading
import hashlib
import base64
from urllib.parse import urljoin, urlparse

import re
import requests
from bs4 import BeautifulSoup
try:
    from selenium import webdriver
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
except ImportError:
    webdriver = None
    WebDriverWait = None
    By = None
    Keys = None

from re import search, findall
# from time import time

try:
    import pandas as pd
except ImportError:
    pd = None

import json
try:
    from ebooklib import epub
except ImportError:
    epub = None
try:
    import tqdm
except ImportError:
    class _TqdmFallback:
        @staticmethod
        def tqdm(iterable=None, *args, **kwargs):
            return iterable if iterable is not None else []
    tqdm = _TqdmFallback()
import mimetypes
import html
from epub_style import epub_style_css, epub_cover_xhtml, epub_chapter_xhtml
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
    

# Source - https://stackoverflow.com/a
# Posted by Michael Mintz, modified by community. See post 'Timeline' for change history
# Retrieved 2026-01-29, License - CC BY-SA 4.0

try:
    from seleniumbase import SB
except ImportError:
    SB = None

def verify_success(sb):
    sb.assert_element('img[alt="Logo Assembly"]', timeout=4)
    sb.sleep(3)



def bypass_capcha(driver, url):
    # with SB(uc=True) as sb:
    #     sb.uc_open_with_reconnect("https://url-cua-ban.com", 3)
    #     # Tự động xử lý nếu gặp checkbox
    #     sb.uc_gui_handle_cf() 
    #     # Tiếp tục các thao tác sau khi xác minh

    # with SB(uc=True) as sb:
    #     sb.uc_open_with_reconnect(url, 3)
    #     try:
    #         verify_success(sb)
    #     except Exception:
    #         if sb.is_element_visible('input[type*="checkbox"]'):
    #             sb.uc_click('input[type*="checkbox"]')
    #         else:
    #             sb.uc_gui_click_captcha()
    #         try:
    #             verify_success(sb)
    #         except Exception:
    #             raise Exception("Detected!")
            
    # return sb.driver.page_source


    driver.get(url)
    time.sleep(5)  # Wait for the page to load
    checkbox = None
    try:
        checkbox = driver.find_element(By.CSS_SELECTOR, 'input[type*="checkbox"]')
    except:
        pass
    if checkbox:
        checkbox.click()
        time.sleep(10)  # Wait for the CAPTCHA to be solved





def login(html_content, driver, log_btn_args={}, user_field_args={}, pass_field_args={}, 
          submit_btn_args={}, credential_name="", anchor_class=None):
    """
    Simulate a login process on a webpage.

    Args:
        html_content (str): The HTML content of the login page.
        log_btn_args (dict): Arguments to locate the login button.
        user_field_args (dict): Arguments to locate the username field.
        pass_field_args (dict): Arguments to locate the password field.
        submit_btn_args (dict): Arguments to locate the submit button.
    Returns:
        webdriver: The Selenium WebDriver after login.
    """

    soup = BeautifulSoup(html_content, 'html.parser')
    # driver.get("data:text/html;charset=utf-8," + html_content)
    is_log_in_btn = soup.find(class_=log_btn_args) is None
    if not is_log_in_btn:
        # print("Already logged in")
        return True
    secrets = json.load(open("data/secrets.json", "r"))
    credentials = secrets[credential_name] if credential_name in secrets else {}
    # print("Credentials found for ", credential_name, ": ", credentials.keys())
    # print("Credentials values: ", credentials.values())
    # print("Starting login process...")
    try:
        # print("Locating login button...")
        # print("log_btn_args: ", log_btn_args)
        login_button = driver.find_element(By.CLASS_NAME, log_btn_args['class_']) if 'class_' in log_btn_args else None
        if login_button:
            # print("Clicking login button...")
            login_button.click()
            driver.implicitly_wait(5)  # Wait up to 5 seconds for elements to load

        # print("Locating login fields...")
        username_field = driver.find_element(By.NAME, user_field_args['attrs']) if 'attrs' in user_field_args else None
        password_field = driver.find_element(By.NAME, pass_field_args['attrs']) if 'attrs' in pass_field_args else None
        submit_button = driver.find_element(By.CLASS_NAME, submit_btn_args['class_']) if 'class_' in submit_btn_args else None
        if username_field and password_field and submit_button:
            # print("Filling in credentials and submitting the form...")
            username_field.send_keys(credentials["username"])  # Replace with actual username
            password_field.send_keys(credentials["password"])  # Replace with actual password
            # username_field.value = credentials["user_name"]
            # password_field.value = credentials["password"]
            submit_button.click()
            driver.implicitly_wait(10)  # Wait up to 10 seconds for elements to load
            # login_btn = BeautifulSoup(driver.page_source, 'html.parser').find(class_=log_btn_args['class_']) if 'class_' in log_btn_args else None
            # if login_btn:
            #     print("Login button still present after submission.")
            try:
                login_btn = driver.find_element(By.TAG_NAME, log_btn_args['class_'])
            except Exception as e:
                # print("Error finding login button after submission:", e)
                # print("Assuming login successful.")
                login_btn = None
            driver.find_element(By.CLASS_NAME, anchor_class)
            if not login_btn:
                # print("Login successful")
                return True
            else:
                # print("Login failed")
                return False
        else:
            # print("Login fields not found")
            return False
    except Exception as e:
        # print(f"Error during login process: {e}")
        return False
    return False
    




def extract_base_url(url):
    """
    Extract the base URL from a given docln.net URL.

    Args:
        url (str): The full URL of the novel.
    Returns:
        str: The base URL of the novel.
    """
    domain = re.search(r'https?://([^/]+)/', url)
    if domain:
        base_url = f"https://{domain.group(1)}"
    else:
        print("Invalid comic link")
        return "error"
    # print("Referrer set to: ", base_url)
    return base_url


def selector_to_css(selector_dict):
    """Convert a format selector dict to a CSS selector string.

    Handles ``id``, ``class_``, ``itemprop``, and ``name`` keys.  Returns ``None``
    when the dict is empty or every value is an empty string.

    Examples
    --------
    >>> selector_to_css({"name": "div", "class_": "chapter-c", "id": "chapter-c"})
    'div.chapter-c#chapter-c'
    >>> selector_to_css({"name": "h3", "class_": "title"})
    'h3.title'
    >>> selector_to_css({}) is None
    True
    """
    if not selector_dict:
        return None
    parts = []
    name = selector_dict.get("name") or ""
    class_ = selector_dict.get("class_") or ""
    id_ = selector_dict.get("id") or ""
    itemprop = selector_dict.get("itemprop") or ""
    if name:
        parts.append(name)
    if class_:
        for cls in class_.split():
            parts.append(f".{cls}")
    if id_:
        parts.append(f"#{id_}")
    if itemprop:
        parts.append(f'[itemprop="{itemprop}"]')
    return "".join(parts) if parts else None


def get_page_content(driver, url):
    """
    Fetch the HTML content of a given URL.

    Args:
        url (str): The URL to fetch.
    Returns:
        str: The HTML content of the page.
    """
    if driver:
        try:
            driver.get(url)
            time.sleep(5)  # Wait for the page to load
            return driver.page_source
        except Exception as e:
            print(f"Error fetching page content with Selenium: {e}")
            return e
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        # test print the prettified html content
        # print("Page content fetched from: ", url)
        # print("page content: ", soup.prettify())  # Print first 500 characters
        # return soup.prettify()
        return response.text
    except Exception as e:
        print(f"Error fetching page content: {e}")
        return e



def get_image_urls(html_content, base_url=None, **img_args):
    """
    Extract image URLs from the HTML content.

    Args:
        html_content (str): The HTML content of the novel page.
        tag (str): The HTML tag that contains the image.
        class_name (str): The class name of the HTML tag.
        img_url_tag (str): The HTML tag for the image URL.
    Returns:
        list: A list of image URLs.
    """
    # print("html_content snippet: ", html_content)
    soup = BeautifulSoup(str(html_content), 'html.parser')
    other_attrs = img_args['other_attr'] if 'other_attr' in img_args else ""
    args = {k: v for k, v in img_args.items() if k != 'other_attr' and v!=""}
    if not args or not other_attrs:
        return []
    # print("Image search args: ", args)
    img_elements = soup.find_all(**args)
    # print("Image elements: ", img_elements)
    img_urls = []
    for img in img_elements:
        try:
            img_url = img.get(other_attrs)
        except:
            img_url = None
        if not img_url:
            match = re.search(rf"{other_attrs}\(['\"]([^'\"]+)['\"]\)", str(img))
            if not match:
                continue
            img_url = match.group(1)
        img_url = img_url.strip()
        # Lazy-loading placeholders and inline data are already embedded in
        # the page; requests cannot download them and they are not chapter
        # assets to save locally.
        if img_url.lower().startswith(("data:", "javascript:", "about:blank", "#")):
            continue
        if base_url and not img_url.startswith(('http://', 'https://')):
            img_url = urljoin(base_url, img_url)
        img_urls.append(img_url)
    # print("Image URLs found: ", len(img_urls))
    return img_urls


def download_image(
    img_url,
    output_dir="outputs/Novel/img",
    ext="jpg",
    name=None,
    img_referrer=False,
    update_log=None,
    referer_url=None,
    cookies=None,
    browser_driver=None,
):
    """
    Download and save images from a list of URLs.

    Args:
        img_urls (list): A list of image URLs to download.
        output_dir (str): The directory to save images.
        ext (str): The image file extension.
    Returns:
        list: A list of local paths to the downloaded images.
    """

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    host = (
        img_referrer
        if isinstance(img_referrer, str)
        else (referer_url or extract_base_url(img_url))
    )
    if img_referrer:
        default_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Referer": host
        }
    else:
        default_headers = None
    #     headers = {
    #         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    #     }
    no_override = True
    temporary_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
    }
    if name:
        local_img_path = f"{output_dir}/{name}.{ext}"
    else:
        local_img_path = f"{output_dir}/img_{int(time.time())}.{ext}"

    tries = 0
    image_host = urlparse(img_url).hostname
    referer_host = urlparse(referer_url).hostname if referer_url else None
    # Do not forward authenticated cookies to a third-party image host.
    request_cookies = cookies if cookies and image_host == referer_host else None

    def save_browser_image():
        """Fetch a same-origin image through Chrome's verified session."""
        if browser_driver is None or image_host != referer_host:
            return ""
        try:
            result = browser_driver.execute_async_script(
                """
                const url = arguments[0];
                const done = arguments[arguments.length - 1];
                const controller = new AbortController();
                const timer = setTimeout(() => controller.abort(), 15000);
                fetch(url, {credentials: 'same-origin', signal: controller.signal})
                    .then(response => {
                        if (!response.ok) throw new Error(`HTTP ${response.status}`);
                        return response.blob();
                    })
                    .then(blob => {
                        if (!blob.size) throw new Error('empty image response');
                        const reader = new FileReader();
                        reader.onloadend = () => {
                            clearTimeout(timer);
                            done({data: reader.result.split(',', 2)[1]});
                        };
                        reader.readAsDataURL(blob);
                    })
                    .catch(error => {
                        clearTimeout(timer);
                        done({error: String(error)});
                    });
                """,
                img_url,
            )
            encoded = result.get("data") if isinstance(result, dict) else None
            if not encoded:
                return ""
            image_bytes = base64.b64decode(encoded, validate=True)
            if not image_bytes:
                return ""
            with open(local_img_path, "wb") as file:
                file.write(image_bytes)
            if update_log:
                update_log(f"Image downloaded through verified browser: {img_url}")
            return local_img_path
        except Exception as exc:
            if update_log:
                update_log(f"Browser image fallback failed for {img_url}: {exc}")
            return ""

    while tries < 5:
        try:
            request_headers = default_headers if no_override else temporary_headers
            with requests.get(
                img_url,
                headers=request_headers,
                cookies=request_cookies,
                timeout=20,
            ) as response:
                if response.status_code == 200 and response.content:
                    with open(local_img_path, "wb") as file:
                        file.write(response.content)
                    if update_log:
                        update_log(f"Image downloaded successfully: {img_url}")
                    return local_img_path
                elif response.status_code == 403:
                    browser_path = save_browser_image()
                    if browser_path:
                        return browser_path
                    if tries > 3:  # Access forbidden, possibly due to referrer issues
                        if no_override:
                            if update_log:
                                update_log(f"Access forbidden for image {img_url} with referrer {host}. Retrying without referrer...")
                            print(f"Access forbidden for image {img_url} with referrer {host}. Retrying without referrer...")
                            no_override = False  # Retry without referrer
                        else:
                            if update_log:
                                update_log(f"Access forbidden for image {img_url} without referrer. Retrying with referrer {host}...")
                            print(f"Access forbidden for image {img_url} without referrer. Retrying with referrer {host}...")
                            temporary_headers = {
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
                                "Referer": referer_url or extract_base_url(img_url)
                            }  # Retry with referrer
                    else:
                        if update_log:
                            update_log(f"Failed to retrieve image {img_url}, status code: {response.status_code}")
                        print(f"Failed to retrieve image {img_url}, status code: {response.status_code}")
                    tries += 1
                    time.sleep(2)
                else:
                    status_description = (
                        "empty response"
                        if response.status_code == 200
                        else f"status code: {response.status_code}"
                    )
                    if update_log:
                        update_log(f"Failed to retrieve image {img_url}, {status_description}")
                    print(f"Failed to retrieve image {img_url}, {status_description}")
                    print("Retrying...")
                    tries += 1
                    time.sleep(2)
        except Exception as e:
            if update_log:
                update_log(f"Error retrieving image {img_url}: {e}")
            print(f"Error retrieving image {img_url}: {e}")
            print("Retrying...")
            tries += 1
            time.sleep(2)
    else:
        if update_log:
            update_log(f"Cannot retrieve image {img_url} after retries.\n")
        print(f"Failed to retrieve image {img_url} after retries.")
        return ""


DEFAULT_COVER_SIZE = (1200, 1800)
DEFAULT_COVER_BACKGROUNDS = "images/default-cover"
DEFAULT_COVER_TEMPLATE = "data/images/backed_cover_2.jpeg"
DEFAULT_COVER_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
DEFAULT_COVER_RESAMPLE = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)


def _stable_index(value, total):
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return int(digest[:12], 16) % total


def _find_default_cover_backgrounds(background_dir):
    if not background_dir or not os.path.isdir(background_dir):
        return []
    backgrounds = []
    for name in sorted(os.listdir(background_dir)):
        path = os.path.join(background_dir, name)
        if os.path.isfile(path) and name.lower().endswith(DEFAULT_COVER_EXTENSIONS):
            backgrounds.append(path)
    return backgrounds


def _load_cover_font(size, bold=False):
    font_paths = [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for font_path in font_paths:
        if os.path.exists(font_path):
            return ImageFont.truetype(font_path, size=size)
    return ImageFont.load_default()


def _text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _break_long_word(draw, word, font, max_width):
    lines = []
    current = ""
    for char in word:
        candidate = current + char
        if current and _text_size(draw, candidate, font)[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _wrap_cover_text(draw, text, font, max_width):
    words = str(text).split()
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        if _text_size(draw, word, font)[0] > max_width:
            broken = _break_long_word(draw, word, font, max_width)
            lines.extend(broken[:-1])
            current = broken[-1] if broken else ""
        else:
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _fit_cover_text(draw, text, max_width, max_height, max_font, min_font, bold=False, max_lines=None):
    for size in range(max_font, min_font - 1, -4):
        font = _load_cover_font(size, bold=bold)
        lines = _wrap_cover_text(draw, text, font, max_width)
        line_height = int(size * 1.22)
        total_height = line_height * len(lines)
        widest = max((_text_size(draw, line, font)[0] for line in lines), default=0)
        if widest <= max_width and total_height <= max_height and (max_lines is None or len(lines) <= max_lines):
            return font, lines, line_height
    font = _load_cover_font(min_font, bold=bold)
    lines = _wrap_cover_text(draw, text, font, max_width)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            lines[-1] = lines[-1].rstrip(". ") + "..."
    return font, lines, int(min_font * 1.22)


def _make_procedural_cover_background(title, size):
    width, height = size
    seed = _stable_index(title, 360)
    base_colors = [
        ((28 + seed) % 95, 42, 72),
        (44, (74 + seed) % 132, 105),
        (90, 54, (120 + seed) % 188),
    ]
    image = Image.new("RGB", size, base_colors[0])
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        c1 = base_colors[0]
        c2 = base_colors[1]
        color = tuple(int(c1[i] * (1 - ratio) + c2[i] * ratio) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    accent = base_colors[2]
    for offset in range(-height, width, 180):
        draw.line([(offset, height), (offset + height, 0)], fill=accent, width=4)
    for i in range(16):
        x = (seed * (i + 7) * 43) % width
        y = (seed * (i + 3) * 71) % height
        radius = 18 + (i % 5) * 8
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(255, 255, 255), width=2)
    return image.filter(ImageFilter.GaussianBlur(0.6))


def _load_cover_background(title, background_dir, size):
    backgrounds = _find_default_cover_backgrounds(background_dir)
    if backgrounds:
        background_path = backgrounds[_stable_index(title, len(backgrounds))]
    elif os.path.exists(DEFAULT_COVER_TEMPLATE):
        background_path = DEFAULT_COVER_TEMPLATE
    else:
        return _make_procedural_cover_background(title, size)
    with Image.open(background_path) as image:
        return ImageOps.fit(image.convert("RGB"), size, method=DEFAULT_COVER_RESAMPLE)


def generate_default_cover(title, author=None, output_dir="outputs/Novel/img",
                           background_dir=DEFAULT_COVER_BACKGROUNDS, output_name="default_cover.png",
                           size=DEFAULT_COVER_SIZE, update_log=None):
    """
    Generate a deterministic default novel cover with title and author text.

    Args:
        title (str): Book title to render.
        author (str): Author name to render.
        output_dir (str): Directory where the generated PNG should be saved.
        background_dir (str): Optional directory of background images.
        output_name (str): Generated cover file name.
        size (tuple): Output cover size.
        update_log (callable): Optional logger.
    Returns:
        str: Local path to the generated cover image.
    """
    if update_log:
        update_log(
            "Default cover generation review: requirements are title/author text, decorative layout, "
            "optional images/default-cover backgrounds, deterministic output, and EPUB-safe PNG. "
            "Risks checked: missing background folder, long text overflow, and output path creation."
        )

    title = str(title or "Untitled").strip() or "Untitled"
    author = str(author or "Unknown author").strip() or "Unknown author"
    os.makedirs(output_dir, exist_ok=True)

    image = _load_cover_background(title, background_dir, size)
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = size

    # Preserve the reference cover's blue chevrons while replacing its
    # placeholder typography with a readable, book-specific layout.
    panel_margin = int(width * 0.07)
    panel_top = int(height * 0.34)
    panel_bottom = int(height * 0.67)
    # The template contains fixed example title text that extends beyond the
    # dynamic panel; mask its entire central band before drawing the new title.
    draw.rectangle(
        (0, int(height * 0.27), width, int(height * 0.73)),
        fill=(247, 250, 251, 255),
    )
    draw.rounded_rectangle(
        (panel_margin, panel_top, width - panel_margin, panel_bottom),
        radius=22,
        fill=(247, 250, 251, 255),
        outline=(207, 225, 234, 230),
        width=4,
    )
    rule_y_top = panel_top + int(height * 0.045)
    rule_y_bottom = panel_bottom - int(height * 0.045)
    draw.line((panel_margin + 70, rule_y_top, width - panel_margin - 70, rule_y_top), fill=(14, 93, 145, 215), width=4)
    draw.line((panel_margin + 70, rule_y_bottom, width - panel_margin - 70, rule_y_bottom), fill=(14, 93, 145, 180), width=3)

    author_panel_top = int(height * 0.85)
    author_panel_bottom = int(height * 0.97)
    draw.rounded_rectangle(
        (panel_margin, author_panel_top, width - panel_margin, author_panel_bottom),
        radius=18,
        fill=(9, 70, 116, 255),
        outline=(224, 242, 248, 200),
        width=3,
    )

    text_draw = ImageDraw.Draw(Image.alpha_composite(image.convert("RGBA"), overlay))
    max_text_width = width - panel_margin * 2 - 120
    title_font, title_lines, title_line_height = _fit_cover_text(
        text_draw, title.upper(), max_text_width, int(height * 0.20), 96, 38, bold=True, max_lines=5
    )
    author_font, author_lines, author_line_height = _fit_cover_text(
        text_draw, author, max_text_width, int(height * 0.075), 46, 24, bold=True, max_lines=2
    )

    final = Image.alpha_composite(image.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(final)
    title_height = len(title_lines) * title_line_height
    title_y = panel_top + int((panel_bottom - panel_top - title_height) * 0.5)
    for line in title_lines:
        line_width, _line_height = _text_size(draw, line, title_font)
        x = (width - line_width) // 2
        draw.text((x + 3, title_y + 3), line, font=title_font, fill=(190, 210, 219, 170))
        draw.text((x, title_y), line, font=title_font, fill=(8, 82, 137, 255))
        title_y += title_line_height

    author_height = len(author_lines) * author_line_height
    author_y = author_panel_top + (author_panel_bottom - author_panel_top - author_height) // 2
    for line in author_lines:
        line_width, _line_height = _text_size(draw, line, author_font)
        x = (width - line_width) // 2
        draw.text((x + 2, author_y + 2), line, font=author_font, fill=(1, 42, 78, 190))
        draw.text((x, author_y), line, font=author_font, fill=(245, 250, 251, 255))
        author_y += author_line_height

    output_path = os.path.join(output_dir, output_name)
    final.convert("RGB").save(output_path, "PNG")
    if update_log:
        update_log(f"Default cover generated: {output_path}")
    return output_path


def ensure_default_cover_for_novel(novel_info, output_dir, update_log=None, persist_path=None):
    cover_image = novel_info.get("cover_image") if novel_info else None
    if cover_image and os.path.exists(cover_image):
        return cover_image

    img_dir = os.path.join(output_dir, "img")
    cover_image = generate_default_cover(
        title=novel_info.get("title") if novel_info else None,
        author=novel_info.get("author") if novel_info else None,
        output_dir=img_dir,
        update_log=update_log,
    )
    novel_info["cover_image"] = cover_image
    if persist_path and os.path.exists(persist_path):
        with open(persist_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["info"]["cover_image"] = cover_image
        with open(persist_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    return cover_image



def get_cover_image_element(
    html_content,
    update_log,
    output_dir="outputs/Novel",
    ext="jpg",
    img_name=None,
    img_referrer=False,
    referer_url=None,
    cookies=None,
    browser_driver=None,
    **cover_args,
):
    """
    Extract the cover image URL (or element) from the HTML content.

    Args:
        html_content (str): The HTML content of the novel page.
        tag (str): The HTML tag that contains the cover image.
        class_name (str): The class name of the HTML tag.
        img_tag (str): The HTML tag for the image element.
    Returns:
        str: The URL of the cover image locally.
    """
    # print("Cover Image args: ", cover_args)
    _cover_args = cover_args['cover_args'] if 'cover_args' in cover_args else cover_args
    soup = BeautifulSoup(html_content, 'html.parser')
    additional_attrs = str(_cover_args['other_attr']) if 'other_attr' in _cover_args else None
    # print("Cover Image additional attribute: ", additional_attrs)
    args = {k: v for k, v in _cover_args.items() if k != 'other_attr' and v!=""}
    # print("Cover Image search args: ", args)
    cover = soup.find(**args)  # Filter out "" values
    update_log(f"Cover image element found with args {args}: {cover is not None}")
    # print("Cover Image element found: ", cover)

    # print("Cover Image element: ", cover)
    # print("Cover attribute to get URL: ", additional_attrs)

    try:
        cover_url = cover.get(additional_attrs)
        update_log(f"Cover image URL found in src: {cover_url}")
    except:
        cover_url = None
    if not cover_url:
        # print("Cover Image URL not in src")
        # print(str(cover))
        # print(img_url_tag)
        update_log(f"Attempting regex extraction to get cover image URL in {cover} using {additional_attrs}.")
        try:
            cover_url = re.search(rf"{additional_attrs}\(['\"]([^'\"]+)['\"]\)", str(cover)).group(1)
        except Exception as e:
            update_log(f"No cover image URL could be extracted: {e}")
            return ""
        # print("Cover Image URL regex match: ", ele)
        # print("Cover Image URL regex group 0: ", ele.group(0))
        # print("Cover Image URL regex group 1: ", ele.group(1))
        # cover_url = ele.group(1)

    if not img_name:
        img_name = "cover"
    # print("Referrer: ", img_referrer)
    # print("Cover Image URL found: ", cover_url)
    cover_image = download_image(
        cover_url,
        output_dir=output_dir,
        ext=ext,
        name=img_name,
        img_referrer=img_referrer,
        referer_url=referer_url,
        cookies=cookies,
        browser_driver=browser_driver,
        update_log=update_log,
    )

    return cover_image



def get_title(html_content, **title_args):
    """
    Extract the title of the novel from the HTML content.

    Args:
        html_content (str): The HTML content of the novel page.
        tag (str): The HTML tag that contains the title.
        class_name (str): The class name of the HTML tag.
    Returns:
        str: The title of the novel.
    """

    soup = BeautifulSoup(html_content, 'html.parser')
    # print("HTML content snippet: ", html_content)
    # print("Title search args: ", title_args)
    # print("content snippet: ", html_content)
    title_ele = soup.find(**title_args)
    # print("Title element found: ", title_ele)
    # title = title_ele.text.strip()
    try:
        # get title text only and replace all " with ' to avoid issues in epub metadata
        title = ''.join(title_ele.find_all(string=True, recursive=False)).strip().replace('"', "'")
    except:
        title = None
    if not title or title == "":
        title = title_ele.text.strip().replace('"', "'") if title_ele else "Unknown Title"

    print("Novel Title: ", title)
    
    return title


def get_genres(driver, html_content, **genre_args):
    """
    Extract the genres of the novel from the HTML content.

    Args:
        html_content (str): The HTML content of the novel page.
        tag (str): The HTML tag that contains the genres.
        class_name (str): The class name of the HTML tag.
    Returns:
        list: A list of genres.
    """
    genre_args = dict(genre_args)
    container_args = genre_args.pop('container', None)
    click_args = genre_args.pop('click', None) or {}
    click_selector = {k: v for k, v in click_args.items() if v != ""}
    if driver and click_selector:
        soup_click = BeautifulSoup(html_content, 'html.parser')
        click_element = soup_click.find(**click_selector)
        if click_element:
            # Simulate a click to reveal full genres
            # driver.get("data:text/html;charset=utf-8," + html_content)
            try:
                clickable = driver.find_element(By.CLASS_NAME, click_args.get('class_')) if 'class_' in click_args else None
                if clickable:
                    clickable.click()
                    time.sleep(2)  # Wait for content to load
                    html_content = driver.page_source
            except Exception as e:
                print(f"Error simulating click for genres: {e}")
    soup = BeautifulSoup(html_content, 'html.parser')
    if container_args:
        container = soup.find(**{k: v for k, v in container_args.items() if v != ""})
        genres_elements = container.find_all(**genre_args) if container else []
    else:
        genres_elements = soup.find_all(**genre_args)
    genres = [genre.text.strip() for genre in genres_elements]
    print("Novel Genres: ", genres)
    return genres



def get_all_other_novel_info(html_content, holders={}, values={}):
    """
    Extract other novel information from the HTML content.

    Args:
        html_content (str): The HTML content of the novel page.
        tag (str): The HTML tag that contains the information.
        class_name (str): The class name of the HTML tag.
    Returns:
        dict: A dictionary of other novel information.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    info_hold = soup.find_all(**{k: v for k, v in holders.items() if v!=""})  # Filter out "" values
    # erase all special characters from info_holder
    info_holder = [re.sub(r'[\^:<|>]+', '', ih.text.strip()) for ih in info_hold]
    info_elements = soup.find_all(**{k: v for k, v in values.items() if v!=""})  # Filter out "" values
    info = [ie.text.strip() for ie in info_elements]

    # print("Other Novel Info Keys: ", info_holder[0])
    # print("Other Novel Info Values: ", info[0])

    other_info = dict(zip(info_holder, info))
    return other_info


def normalize_text(text):
    """
    Normalize text by removing extra whitespace and newlines.

    Args:
        text (str): The text to normalize.
    Returns:
        str: The normalized text.
    """
    norm_text = re.sub(r'[ ]+', ' ', text)
    norm_text = re.sub(r'[ ]*\n+[ ]*', '\n', norm_text)
    return norm_text.strip()


def get_description(driver, html_content, **desc_args):
    """
    Extract the description of the novel from the HTML content.

    Args:
        html_content (str): The HTML content of the novel page.
        tag (str): The HTML tag that contains the description.
        class_name (str): The class name of the HTML tag.
    Returns:
        str: The description of the novel.
    """
    desc_args = dict(desc_args)
    click_args = desc_args.pop('click', None) or {}
    click_selector = {k: v for k, v in click_args.items() if v != ""}
    if driver and click_selector:
        soup_click = BeautifulSoup(html_content, 'html.parser')
        click_element = soup_click.find(**click_selector)
        if click_element:
            # Simulate a click to reveal full description
            # driver.get("data:text/html;charset=utf-8," + html_content)
            try:
                clickable = driver.find_element(By.CLASS_NAME, click_args.get('class_')) if 'class_' in click_args else None
                if clickable:
                    clickable.click()
                    time.sleep(2)  # Wait for content to load
                    html_content = driver.page_source
            except Exception as e:
                print(f"Error simulating click for description: {e}")
    soup = BeautifulSoup(html_content, 'html.parser')
    args = {k: v for k, v in desc_args.items() if k != 'text' and v!=""}
    if desc_args.get('text', True):
        description = normalize_text(
        # description = (
            soup.find(**args).text.strip()
            )
    else:
        description = normalize_text(
        # description = (
            soup.find(**args).prettify()
            )
    # print("Novel Description: ", description)
    return description


def get_all_volume(html_content, vol_sect={}, vol_title={}, vol_cover={}, vol_chap={}, base_url=None, update_log=None):
    """
    Extract the volume information of the novel from the HTML content.

    Args:
        html_content (str): The HTML content of the novel page.
        tag (str): The HTML tag that contains the volume information.
        class_name (str): The class name of the HTML tag.
    Returns:
        list: A list of volume inner HTML code.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    vol_s = {k: v for k, v in vol_sect.items() if v!=""}  # Filter out "" values
    volumes_elements = soup.find_all(**vol_s)
    vol_s_disabled = vol_s.copy()
    vol_s_disabled['class_'] = 'disabled'
    disabled_volumes_elements = soup.find_all(**vol_s_disabled)
    volumes = [volume for volume in volumes_elements if volume not in disabled_volumes_elements]
    # print("Total Volume sections found: ", volumes)

    if 'name' in vol_title:
        vol_t = vol_title
    else:
        vol_t = {k: v for k, v in vol_title.items() if v!=""}  # Filter out "" values
    if vol_cover:
        cover_output_dir=vol_cover['output_dir'] if 'output_dir' in vol_cover else "outputs/Novel/img"
        img_referrer = vol_cover['img_referrer'] if 'img_referrer' in vol_cover else False
        vol_c = {k: v for k, v in vol_cover.items() if k not in ['output_dir', 'name', 'img_referrer'] and v!=""}  # Filter out "" values

    chap_args = {k: v for k, v in vol_chap.items() if v!=""}  # Filter out "" values

    vol_info = []

    for vol in range(len(volumes)):
        # print("vol: ", volumes[vol].prettify())
        v = {}
        if 'vol_name' in vol_t:
            v_title = vol_t['vol_name']
        else:
            v_title = volumes[vol].find(**vol_t).text.strip()
        print("Volume Title found: ", v_title)
        v['title'] = v_title
        
        if vol_cover:
            # print("vol args: ", vol_c)
            vol_cover_url = get_image_urls(volumes[vol], base_url=base_url, **vol_c)
            print("Volume Cover URLs found: ", vol_cover_url)
            update_log(f"Volume {v_title} cover URL found: {vol_cover_url}")
            v_cover = download_image(vol_cover_url[0], output_dir=cover_output_dir, 
                                    ext="jpg", name=f"vol_{vol+1}_cover", 
                                    img_referrer=img_referrer, update_log=update_log)
            update_log(f"Volume {v_title} cover downloaded: {v_cover}")
            print("Volume Cover found: ", vol_cover_url)
        else:
            v_cover = ""
        v['cover_image'] = v_cover

        chap_list = volumes[vol].find(**chap_args)
        # print("Chapter list element found: ", chap_list)
        chap_links = [a.get('href') for a in chap_list.find_all('a')] if chap_list else []
        if base_url:
            chap_links = [base_url + link if not link.startswith('http') else link for link in chap_links]
        print("Volume Chapter links found: ", len(chap_links))
        update_log(f"Volume {v_title} chapter links found: {len(chap_links)}")
        v['chapter_links'] = chap_links
        vol_info.append(v)

    print("Novel Volumes found: ", len(volumes))
    return vol_info


def get_volume_info_from_link(driver, base_url, vol_url, title_args={}, cover_args={}, chap_list_args={},
                              update_log=None, page_fetcher=None):
    # print("Fetching volume info from link: ", vol_url)
    # print("Title args: ", title_args)
    content = page_fetcher(vol_url) if page_fetcher else get_page_content(driver, vol_url)
    # print("Volume page content fetched from: ", vol_url)
    # print("Content snippet: ", content)
    # def get_cover_image_element(html_content, tag=None, class_name=None, 
    #                         attr=None, img_url_tag=None, output_dir="outputs/Novel", 
    #                         ext="jpg", name=None):
    vol_title = get_title(content, **title_args)
    print("Volume Title found: ", vol_title)
    
    if cover_args:
        # cover_output_dir=cover_args['output_dir'] if 'output_dir' in cover_args else "outputs/Novel/img"
        # cover_name=cover_args['name'] if 'name' in cover_args else f"{vol_title}_cover"
        # img_referrer = cover_args['img_referrer'] if 'img_referrer' in cover_args else False
        # c_args = {k: v for k, v in cover_args.items() if k not in ['output_dir', 'name', 'img_referrer'] and v!=""}
        cover_args["update_log"] = update_log
        cover_url = get_cover_image_element(content, **cover_args)
    else:
        cover_url = ""
    
    soup = BeautifulSoup(content, 'html.parser')
    chap_list_element = soup.find(**chap_list_args)
    chap_links = [base_url + a.get('href') if not a.get('href').startswith('http') else a.get('href') for a in chap_list_element.find_all('a')]
    print("Volume Chapter links found: ", len(chap_links))

    return {
        "title": vol_title,
        "cover_image": cover_url,
        "chapter_links": chap_links
    }


# def get_chapter_page(url):
#     """
#     Fetch the HTML content of a given chapter URL.

#     Args:
#         url (str): The URL of the chapter to fetch.
#     Returns:
#         str: The HTML content of the chapter page.
#     """
#     try:
#         response = requests.get(url)
#         soup = BeautifulSoup(response.text, 'html.parser')
#         return soup.prettify()
#     except requests.RequestException as e:
#         print(f"Error fetching chapter content: {e}")
#         return None


def get_chapter_title(html_content, chap_args={}, update_log=None):
    """
    Extract the title of a chapter from the HTML content.

    Args:
        html_content (str): The HTML content of the chapter page.
        tag (str): The HTML tag that contains the chapter title.
        class_name (str): The class name of the HTML tag.
    Returns:
        str: The title of the chapter.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    args = {k: v for k, v in chap_args.items() if v!=""}
    # print("Chapter Title search args: ", chap_args)

    chapter_title_ele = soup.find(**args) if args else None
    # print("Chapter Title element found: ", chapter_title_ele)
    try:
        if chapter_title_ele:
            chapter_title = chapter_title_ele.text.strip()
        else:
            title_element = soup.find("title") or soup.find(["h1", "h2", "h3"])
            chapter_title = title_element.text.strip() if title_element else "Untitled Chapter"
            if update_log:
                update_log("Chapter title selector is empty or missing; using page title/headline fallback.")
        chapter_title = normalize_text(chapter_title)
        if update_log:
            update_log(f"Chapter title found: {chapter_title}")
    except:
        with open("error_chapter.html", "w", encoding="utf-8") as f:
            f.write(soup.prettify())
        # terminate the program if chapter title cannot be found
        if update_log:
            update_log("Chapter title not found, check error_chapter.html for details.")
        raise Exception("Chapter title not found, check error_chapter.html for details.")
        
    # print("Chapter Title: ", chapter_title)
    return chapter_title


def get_chapter_content(html_content, kwargs, update_log=None):
    """
    Extract the main content of a chapter from the HTML content.

    Args:
        html_content (str): The HTML content of the chapter page.
        tag (str): The HTML tag that contains the chapter content.
        class_name (str): The class name of the HTML tag.
    Returns:
        str: The main content of the chapter.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    args = {k: v for k, v in kwargs.items() if v != ""}

    if args:
        chapter_element = soup.find(**args)
        if chapter_element is None:
            message = f"Chapter content not found with selector: {args}"
            if update_log:
                update_log(message)
            with open("error_chapter_content.html", "w", encoding="utf-8") as f:
                f.write(soup.prettify())
            raise ValueError(f"{message}. Check error_chapter_content.html for details.")
    else:
        chapter_element = soup.find("body") or soup
        if update_log:
            update_log("Chapter content selector is empty; using page body as chapter content.")

    chapter_content = chapter_element.prettify()
    if update_log:
        update_log("Chapter body extracted successfully.")
    return chapter_content



def make_chap_img_local(chap_content, output_dir, img_prefix="", ext="jpg", update_log=None):
    # output_dir="outputs/Novel/img"
    """
    Download and save all images in the chapter content locally.

    Args:
        chap_content (str): The HTML content of the chapter.
        output_dir (str): The directory to save images.
        ext (str): The image file extension.
    Returns:
        str: The local path to folder containing chapter images.
    """

    img_folder = f"{output_dir}"

    if not os.path.exists(img_folder):
        os.makedirs(img_folder)

    soup = BeautifulSoup(chap_content, 'html.parser')
    img_tags = soup.find_all('img')
    for idx, img in enumerate(img_tags):
        # print("Processing image: ", img)
        img_url = img.get('src')
        host = extract_base_url(img_url)
        header = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
            "Referer": img_url
        }
        # print(f"Downloading image: {img_url}")
        tries = 0
        try:
            with requests.get(img_url, headers=header) as response:
                local_img_path = f"{img_folder}/{img_prefix}_img{idx}.{ext}"
                if response.status_code == 200:
                    with open(local_img_path, "wb") as file:
                        file.write(response.content)
                    # img = f"<img src='{local_img_path}' alt='img'/>"
                    # soup.find_all('img')[idx].replace_with(BeautifulSoup(img, 'html.parser'))
                    img['src'] = local_img_path
                    img['alt'] = 'img'
                    time.sleep(1)  # Be polite and avoid overwhelming the server
                    update_log(f"Image downloaded successfully: {img_url}")
                else:
                    update_log(f"Failed to retrieve image {img_url}, status code: {response.status_code}")
                    print(f"Failed to retrieve image {img_url}, status code: {response.status_code}")
                    print("Retrying...")
                    while tries < 5:
                        tries += 1
                        time.sleep(2)
                        with requests.get(img_url, headers=header) as response:
                            if response.status_code == 200:
                                print(f"Successfully retrieved image {img_url} on retry {tries}.")
                                with open(local_img_path, "wb") as file:
                                    file.write(response.content)
                                # img = f"<img src='{local_img_path}' alt='img'/>"
                                # soup.find_all('img')[idx].replace_with(BeautifulSoup(img, 'html.parser'))
                                img['src'] = local_img_path
                                img['alt'] = 'img'
                                break
                    else:
                        print(f"Failed to retrieve image {img_url} after retries.")
                        print("Trying again with Selenium...")
                        tries = 0
                        while tries < 5:
                            tries += 1
                            time.sleep(2)
                            try:
                                from utils.fetcher import chrome_options
                                driver = webdriver.Chrome(options=chrome_options(headless=True))
                                driver.get(img_url)
                                img_data = driver.find_element(By.TAG_NAME, 'img').screenshot_as_png
                                with open(local_img_path, "wb") as file:
                                    file.write(img_data)
                                img['src'] = local_img_path
                                img['alt'] = 'img'
                                driver.quit()
                                update_log(f"Image retrieved successfully with Selenium: {img_url}")
                                print(f"Successfully retrieved image {img_url} on retry {tries} in Selenium.")
                                break
                            except Exception as e:
                                update_log(f"Error retrieving image {img_url} with Selenium: {e}")
                                print(f"Error retrieving image {img_url}: {e}")
                                continue
                        else:
                            print(f"Failed to retrieve image {img_url} after retries.")
                            img['src'] = ""
                            img['alt'] = 'img'
        except Exception as e:
            update_log(f"Error retrieving image {img_url}: {e}")
            print(f"Error retrieving image {img_url}: {e}")
            print("Retrying...")
            while tries < 5:
                tries += 1
                time.sleep(2)
                try:
                    with requests.get(img_url, headers=header) as response:
                        local_img_path = f"{img_folder}/img_{idx}.{ext}"
                        if response.status_code == 200:
                            with open(local_img_path, "wb") as file:
                                file.write(response.content)
                            # img = f"<img src='{local_img_path}' alt='img'/>"
                            # soup.find_all('img')[idx].replace_with(BeautifulSoup(img, 'html.parser'))
                            img['src'] = local_img_path
                            img['alt'] = 'img'
                            update_log(f"Image downloaded successfully: {img_url}")
                            break
                except Exception as e:
                    update_log(f"Error retrieving image {img_url}: {e}")
                    print(f"Error retrieving image {img_url}: {e}")
                    continue
            else:
                print(f"Failed to retrieve image {img_url} after retries.")
                img['src'] = ""
                img['alt'] = 'img'
    return soup.prettify(), img_folder


def _safe_epub_filename(prefix, label="", ext=".xhtml", max_label_length=40):
    raw_label = normalize_text(str(label or ""))
    ascii_label = raw_label.encode("ascii", "ignore").decode("ascii").lower()
    ascii_label = re.sub(r"[^a-z0-9]+", "-", ascii_label).strip("-")
    if len(ascii_label) > max_label_length:
        ascii_label = ascii_label[:max_label_length].rstrip("-")

    digest_source = f"{prefix}:{raw_label}"
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:10]
    parts = [str(prefix).lower().replace(" ", "-").strip("-")]
    if ascii_label:
        parts.append(ascii_label)
    parts.append(digest)
    return "_".join(parts) + ext


def _clean_epub_display_title(title, fallback, chapter_index=None):
    normalized = normalize_text(str(title or ""))
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    chapter_pattern = re.compile(r"(chương|chapter|chap)\s*\d+[^|\n]{0,140}", re.IGNORECASE)
    chapter_candidates = []

    for line in lines:
        for match in chapter_pattern.finditer(line):
            chapter_candidates.append(normalize_text(match.group(0)).strip(" -_|"))

    if chapter_index is not None:
        exact_pattern = re.compile(rf"^(chương|chapter|chap)\s*0*{int(chapter_index)}\b", re.IGNORECASE)
        for candidate in chapter_candidates:
            if exact_pattern.search(candidate) and ":" in candidate and len(candidate) <= 140:
                return candidate
        for candidate in chapter_candidates:
            if exact_pattern.search(candidate) and len(candidate) <= 140:
                return candidate

    for candidate in chapter_candidates:
        if "end" not in candidate.lower() and ":" in candidate and len(candidate) <= 140:
            return candidate

    chapter_candidates = [candidate for candidate in chapter_candidates if "end" not in candidate.lower()]

    if len(chapter_candidates) > 1:
        candidate = chapter_candidates[1]
        return candidate if len(candidate) <= 140 else fallback
    if chapter_candidates:
        candidate = chapter_candidates[0]
        return candidate if len(candidate) <= 140 else fallback

    for line in lines:
        if len(line) <= 180:
            return line.split("|")[0].strip()

    return fallback



def make_chapter_epub(chapter_element, title_added, idx, update_log):
    """
    Create EPUB chapter element from chapter json information.
    Args:
        chapter_element (dict): The chapter information in dictionary format.
        chapter_element included: {chapter_title, chapter_content, chapter_img_folder}
        Using style from epub_style folder.
        title_added (tuple): A tuple containing strings to add before and after the title.
    Returns:
        epub.EpubHtml: The EPUB chapter element.
    """
    style = epub.EpubItem(
            file_name="style.css",
            media_type="text/css",
            content=epub_style_css()
        )

    chap_ele = BeautifulSoup(chapter_element['chapter_content'], 'html.parser')
    images = chap_ele.find_all('img')
    img_map = {}
    epub_images = []
    for img in images:
        src = img.get('src')
        if not src:
            update_log(f"Image source not found for img: {img}")
            continue
        else:
            if "http" in src:
                img.decompose()
                update_log(f"External image source found and removed: {src}")
                continue
        try:
            with open(src, "rb") as img_file:
                img_content = img_file.read()

            mime_type = f"image/jpeg" 

            epub_img_name = src.split("/")[-1]
            epub_img_path = src
            epub_image = epub.EpubItem(
                file_name=epub_img_path,
                media_type=mime_type,
                content=img_content
            )
            # style.add_item(epub_image)
            img_map[src] = epub_img_path
            img['src'] = epub_img_path
            epub_images.append(epub_image)
            update_log(f"Image processed and added to EPUB: {src}")
        except Exception as e:
            update_log(f"Error processing image {src}: {e}")
            print(f"Error reading image file {src}: {e}")
            img.decompose()
            
        

    chapter_title = _clean_epub_display_title(
        chapter_element.get('chapter_title'),
        fallback=f"Chapter {idx}",
        chapter_index=idx,
    )
    title_prefix = normalize_text(title_added[0]) if title_added[0] else ""
    title_suffix = normalize_text(title_added[1]) if title_added[1] else ""
    display_title = " ".join(part for part in [title_prefix, chapter_title, title_suffix] if part).strip()
    chapter_file_name = _safe_epub_filename(f"{title_prefix or 'chapter'}-{idx:04d}", "", ".xhtml")

    chapter = epub.EpubHtml(
        file_name=chapter_file_name,
        title=chapter_title,
        lang="vi",

        content=f"""
        <div class="chapter" id="chapter-{idx}">
            <h1>{html.escape(display_title)}</h1>
        </div>
        {chap_ele.prettify()}
        """
    )
    chapter.add_link(
        href=style.file_name,
        rel="stylesheet",
        type="text/css",
    )
    for epub_image in epub_images:
        chapter.add_item(epub_image)


    update_log(f"Chapter '{chapter_title}' processed with {len(epub_images)} images.")

    return chapter, epub_images


def make_volume_epub(volume_info, chapter_list, title_added=("", "")):
    """
    Create EPUB volume from novel information and selected chapters.
    Args:
        novel_info (dict): The novel information in dictionary
        novel_info included: {title, cover_image, description(optional)}
        chapter_list (list): The list of chapter epub elements (got from make_chapter_epub).
    Returns:
        epub.EpubHtml: The EPUB volume element.
    """
    print(f"Volume cover: {volume_info['cover_image']}")
    cover_img = ""
    if volume_info['cover_image'] and os.path.exists(volume_info['cover_image']):
        cover_img = volume_info['cover_image']
    else:
        print("Volume cover image not found, skipping cover image.")
        cover_img = "data/images/backed_cover_1.png"
    with open(cover_img, "rb") as cover_file:
        vol_image = epub.EpubItem(
            file_name=cover_img,
            media_type="image/jpeg" if cover_img.endswith(".jpg") or cover_img.endswith(".jpeg") else "image/png",
            content=cover_file.read()
        )

    volume = epub.EpubHtml(
        file_name=_safe_epub_filename("volume", volume_info.get("title", "volume"), ".xhtml"),
        title=volume_info['title'],
        lang="vi",

        content=f"""
        <div class="volume" id="volume-{volume_info['title']}">
            <h1>{html.escape(' '.join(part for part in [title_added[0], volume_info['title'], title_added[1]] if part).strip())}</h1>
        </div>

        <div id="volume-cover">
            <img src="{volume_info['cover_image']}" alt="Volume Cover Image" style="width:100%; height:auto;" />
        </div>
        """
    )
    style = epub.EpubItem(
            file_name="style.css",
            media_type="text/css",
            content=epub_style_css()
        )


        
       

    volume.add_link(
        href=style.file_name,
        rel="stylesheet",
        type="text/css",
    )
    volume.add_item(vol_image)

    # toc = []
    # spine = []
    # vol_images = []

    # # toc.append(volume)
    # # spine.append(volume)

    # for chapter, epub_images in chapter_list:
    #     vol_images.extend(epub_images)

    return vol_image, volume, chapter_list

def make_book_epub(novel_info, volume_list, title_added, output_path, ebook_name):
    """
    Create EPUB book from novel information and selected volumes.
    Args:
        novel_info (dict): The novel information in dictionary
        novel_info included: {
            title, cover_image, description(optional), 
            genres(list), author, other_info(optional),
            num_chapters(optional), num_volumes(optional),
            novel_url
            }
        volume_list (list): The list of volume epub elements (got from make_volume_epub).
        volume_list included: [(vol_image, vol_element, chapter_list), ...]
        title_added (tuple): A tuple containing strings to add before and after the title.
        output_path (str): The directory to save the EPUB file.
    Returns:
        epub.EpubBook: The complete EPUB book element.
    """
    # Create an EPUB book
    book = epub.EpubBook()

    # Set metadata
    book.set_identifier(f"{hash(novel_info['title'])}-{hash(novel_info['author'])}")
    novel_title = title_added[0] + " " + novel_info["title"] + " " + title_added[1]
    book.set_title(novel_title.strip())
    book.set_language("vi")
    book.add_author(novel_info["author"])
    book.add_metadata("DC", "description", novel_info["description"] if "description" in novel_info else "<p>Không có mô tả</p>")
    for genre in novel_info['genres']:
        book.add_metadata("DC", "subject", genre)

    style = epub.EpubItem(
            file_name="style.css",
            media_type="text/css",
            content=epub_style_css()
        )
    
    book.add_item(style)

    book.set_template("cover", epub_cover_xhtml())
    book.set_template("chapter", epub_chapter_xhtml())

    toc = []
    spine = []

    if "cover_image" in novel_info and novel_info["cover_image"]:
        cover_name = os.path.basename(novel_info["cover_image"])
        cover_media_type = mimetypes.guess_type(cover_name)[0] or "image/png"
        with open(f"{novel_info['cover_image']}", "rb") as cover_file:
            cover_content = cover_file.read()
            book.set_cover(cover_name, cover_content)

        # ``set_cover`` also creates cover.xhtml.  Supply a proper image media
        # type for the generated manifest item so readers can render JPEGs.
        cover_image_item = book.get_item_with_id("cover-img")
        if cover_image_item:
            cover_image_item.media_type = cover_media_type
        
        spine.append("cover")

        toc.append(epub.Link("cover.xhtml", "Bìa sách", "cover"))
    

    intro_content = f"""
    <h1>{novel_info['title']}</h1>
    <p>Tác giả: {novel_info['author']}</p>
    """
    if not novel_info["other_info"]:
        for key, value in novel_info["other_info"].items():
            intro_content += f"<p>{key}: {value}</p>"
    if len(novel_info["description"]) > 0:
        intro_content += f"<h2>Mô tả:</h2><p>{novel_info['description']}</p>"
    intro_content += f"<p>Thể loại: {', '.join(novel_info['genres']) if novel_info['genres'] else 'Khác'}</p>"
    intro_content += f"<p>Link chính: {novel_info['novel_url']}</p>"
    intro_content += """
    <p>This project is inspired and copy style from <b>lncrawler</b> project by <a href="https://github.com/dipu-bd/lightnovel-crawler">dipu-bd/lightnovel-crawler</a>.</p>
    <p>If you like this project, please give some time to visit the original project.</p>
    <p><i>Generated by WikiCrawler of Nguyen Hai Dang</i></p>
    """
    # Add introduction
    intro = epub.EpubHtml(
        title=f"Giới thiệu nội dung",
        file_name="intro.xhtml",
        # lang='vi',
        # <p>Thể loại: {', '.join(self.novel_info['genres']) if self.novel_info['genres'] else 'Khác'}</p>
        content=intro_content
    )
    intro.add_link(
        href="style.css",
        rel="stylesheet",
        type="text/css",
    )
    book.add_item(intro)
    spine.append(intro)
    toc.append(intro)

    # if novel_info["num_chapters"] <= 100:
    #     spine.append("nav")
    
    # Add volumes and chapters
    for volume_data in volume_list:
        
        vol_image, volume_element, chapter_list = volume_data

        book.add_item(vol_image)
        book.add_item(volume_element)
        toc.append(volume_element)
        spine.append(volume_element)

        # for chap in volume_toc:
        #     book.add_item(chap)
        #     toc.append(chap)
        # for chap in volume_spine:
        #     spine.append(chap)
        for chapter_element, epub_images in chapter_list:
            for epub_image in epub_images:
                book.add_item(epub_image)
            book.add_item(chapter_element)
            toc.append(chapter_element)
            spine.append(chapter_element)

    
    book.toc = toc
    book.spine = spine

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(f"{output_path}/{ebook_name}", book, {})

    print(f"Epub file '{ebook_name}' created successfully.")

    return f"{output_path}/{ebook_name}"


def get_data_base():
    db = pd.read_csv("data/aliases.csv")

    return db
