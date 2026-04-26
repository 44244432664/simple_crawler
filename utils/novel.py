import os
import time
import threading

import re
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


import pandas as pd

import json
from ebooklib import epub
import tqdm
import mimetypes
from epub_style import epub_style_css, epub_cover_xhtml, epub_chapter_xhtml
    

# Source - https://stackoverflow.com/a
# Posted by Michael Mintz, modified by community. See post 'Timeline' for change history
# Retrieved 2026-01-29, License - CC BY-SA 4.0

from seleniumbase import SB

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
        response = requests.get(url)
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
    other_attrs = img_args['other_attr'] if 'other_attr' in img_args else {}
    args = {k: v for k, v in img_args.items() if k != 'other_attr' and v!=""}
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
            img_url = re.search(rf"{other_attrs}\(['\"]([^'\"]+)['\"]\)", str(img)).group(1)
        if base_url and not img_url.startswith('http'):
            img_url = base_url + img_url
        img_urls.append(img_url)
    # print("Image URLs found: ", len(img_urls))
    return img_urls


def download_image(img_url, output_dir="outputs/Novel/img", ext="jpg", name=None, img_referrer=False, update_log=None):
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

    host = img_referrer if isinstance(img_referrer, str) else extract_base_url(img_url)
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
    tries = 0
    while tries < 5:
        try:
            request_headers = default_headers if no_override else temporary_headers
            with requests.get(img_url, headers=request_headers) as response:
                if name:
                    local_img_path = f"{output_dir}/{name}.{ext}"
                else:
                    local_img_path = f"{output_dir}/img_{int(time.time())}.{ext}"
                if response.status_code == 200:
                    with open(local_img_path, "wb") as file:
                        file.write(response.content)
                    update_log(f"Image downloaded successfully: {img_url}")
                    return local_img_path
                elif response.status_code == 403 and tries > 3:  # Access forbidden, possibly due to referrer issues
                    if no_override:
                        update_log(f"Access forbidden for image {img_url} with referrer {host}. Retrying without referrer...")
                        print(f"Access forbidden for image {img_url} with referrer {host}. Retrying without referrer...")
                        no_override = False  # Retry without referrer
                    else:
                        update_log(f"Access forbidden for image {img_url} without referrer. Retrying with referrer {host}...")
                        print(f"Access forbidden for image {img_url} without referrer. Retrying with referrer {host}...")
                        temporary_headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
                            "Referer": extract_base_url(img_url)
                        }  # Retry with referrer
                    tries += 1
                    time.sleep(2)
                else:
                    update_log(f"Failed to retrieve image {img_url}, status code: {response.status_code}")
                    print(f"Failed to retrieve image {img_url}, status code: {response.status_code}")
                    print("Retrying...")
                    tries += 1
                    time.sleep(2)
        except Exception as e:
            update_log(f"Error retrieving image {img_url}: {e}")
            print(f"Error retrieving image {img_url}: {e}")
            print("Retrying...")
            tries += 1
            time.sleep(2)
    else:
        update_log(f"Cannot retrieve image {img_url} after retries.\n")
        print(f"Failed to retrieve image {img_url} after retries.")
        return ""



def get_cover_image_element(html_content, update_log, output_dir="outputs/Novel", ext="jpg", img_name=None, 
                            img_referrer=False, **cover_args):
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
        cover_url = re.search(rf"{additional_attrs}\(['\"]([^'\"]+)['\"]\)", str(cover)).group(1)
        # print("Cover Image URL regex match: ", ele)
        # print("Cover Image URL regex group 0: ", ele.group(0))
        # print("Cover Image URL regex group 1: ", ele.group(1))
        # cover_url = ele.group(1)

    if not img_name:
        img_name = "cover"
    # print("Referrer: ", img_referrer)
    # print("Cover Image URL found: ", cover_url)
    cover_image = download_image(cover_url, output_dir=output_dir, ext=ext, name=img_name, 
                                 img_referrer=img_referrer, update_log=update_log)

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
    if driver and 'click' in genre_args:
        click_args = genre_args['click']
        soup_click = BeautifulSoup(html_content, 'html.parser')
        click_element = soup_click.find(**{k: v for k, v in click_args.items() if v!=""})
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
        del genre_args['click']
    soup = BeautifulSoup(html_content, 'html.parser')
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
    if driver and 'click' in desc_args:
        click_args = desc_args['click']
        soup_click = BeautifulSoup(html_content, 'html.parser')
        click_element = soup_click.find(**{k: v for k, v in click_args.items() if v!=""})
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
        del desc_args['click']
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
        chap_links = [a.get('href') for a in chap_list.find_all('a')]
        if base_url:
            chap_links = [base_url + link if not link.startswith('http') else link for link in chap_links]
        print("Volume Chapter links found: ", len(chap_links))
        update_log(f"Volume {v_title} chapter links found: {len(chap_links)}")
        v['chapter_links'] = chap_links
        vol_info.append(v)

    print("Novel Volumes found: ", len(volumes))
    return vol_info


def get_volume_info_from_link(driver, base_url, vol_url, title_args={}, cover_args={}, chap_list_args={}, update_log=None):
    # print("Fetching volume info from link: ", vol_url)
    # print("Title args: ", title_args)
    content = get_page_content(driver, vol_url)
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

    chapter_title_ele = soup.find(**args)
    # print("Chapter Title element found: ", chapter_title_ele)
    try:
        chapter_title = chapter_title_ele.text.strip()
        update_log(f"Chapter title found: {chapter_title}")
    except:
        with open("error_chapter.html", "w", encoding="utf-8") as f:
            f.write(soup.prettify())
        # terminate the program if chapter title cannot be found
        upate_log("Chapter title not found, check error_chapter.html for details.")
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
    args = {k: v for k, v in kwargs.items() if v!=""}
    try:
        chapter_content = soup.find(**args).prettify()
        update_log("Chapter body extracted successfully.")
    except Exception as e:
        update_log(f"Error occurred while extracting chapter content: {e}")
        # raise Exception(f"Error occurred while extracting chapter content: {e}")
    # print("Chapter Content extracted")
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
                                driver = webdriver.Chrome()  # Make sure to have the appropriate WebDriver installed
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
            
        

    chapter = epub.EpubHtml(
        file_name=f"{title_added[0]}_{chapter_element['chapter_title'].replace(' ', '_')}{title_added[1]}.xhtml",
        title=chapter_element['chapter_title'],
        lang="vi",

        content=f"""
        <div class="chapter" id="chapter-{idx}">
            <h1>{title_added[0]} {chapter_element['chapter_title']} {title_added[1]}</h1>
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


    update_log(f"Chapter '{chapter_element['chapter_title']}' processed with {len(epub_images)} images.")

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
    vol_image = epub.EpubItem(
        file_name=cover_img,
        media_type="image/jpeg" if cover_img.endswith(".jpg") or cover_img.endswith(".jpeg") else "image/png",
        content=open(cover_img, "rb").read()
    )

    volume = epub.EpubHtml(
        file_name=f"{title_added[0]}_{volume_info['title'].replace(' ', '_')}{title_added[1]}.xhtml",
        title=volume_info['title'],
        lang="vi",

        content=f"""
        <div class="volume" id="volume-{volume_info['title']}">
            <h1>{title_added[0]} {volume_info['title']} {title_added[1]}</h1>
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
        cover_name = novel_info["cover_image"].split("/")[-1]
        with open(f"{novel_info['cover_image']}", "rb") as cover_file:
            cover_content = cover_file.read()
            book.set_cover(cover_name, cover_content, f"image/{cover_name.split('.')[-1]}")
        
        spine.append("cover")

        cover_item = epub.EpubHtml(
            uid="cover",
            file_name=cover_name,
            media_type=f"image/{cover_name.split('.')[-1]}",
            content=f"""
            <div id="cover">
                <img src="{novel_info['cover_image']}" alt="Cover Image" style="width:100%; height:auto;" />
            </div>
            """
        )

        cover_item.add_link(
            href="style.css",
            rel="stylesheet",
            type="text/css",
            )
        
        book.add_item(cover_item)
        spine.append(cover_item)
        toc.append(epub.Link(cover_name, "Bìa sách", "cover"))
    

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
