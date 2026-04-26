import requests

from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from bs4 import BeautifulSoup
import re
from PIL import Image
from PIL import ImageFile
import zipfile
import os
import time
import math
import tqdm

import pandas as pd

import json

from ebook.make_pdf import create_pdf
from ebook.make_cbz import create_cbz

from utils.novel import login, extract_base_url, get_page_content


def get_author(html_content, **author_ele_args):
    soup = BeautifulSoup(html_content, 'html.parser')
    author_ele = soup.find(**author_ele_args)
    if author_ele:
        return author_ele.text.strip()
    return "Unknown Author"

def get_title(html_content, **title_ele_args):
    soup = BeautifulSoup(html_content, 'html.parser')
    title_ele = soup.find(**title_ele_args)
    if title_ele:
        return title_ele.text.strip()
    return "Untitled"

def get_genres(html_content, **genre_ele_args):
    soup = BeautifulSoup(html_content, 'html.parser')
    genre_ele = soup.find_all(**genre_ele_args)
    genres = []
    if genre_ele:
        genres = [g.text.strip() for g in genre_ele]
    return genres

def get_description(html_content, **desc_ele_args):
    soup = BeautifulSoup(html_content, 'html.parser')
    desc_ele = soup.find(**desc_ele_args)
    if desc_ele:
        return desc_ele.text.strip()
    return "No Description"

def get_other_info(html_content, **other_info_ele_args):
    soup = BeautifulSoup(html_content, 'html.parser')
    holder_ele = soup.find_all(**other_info_ele_args['holder'])
    value_ele = soup.find_all(**other_info_ele_args['value'])

    holder = [h.text.strip() for h in holder_ele]
    value = [v.text.strip() for v in value_ele]

    other_info = {}
    if holder:
        other_info = {k: v for k, v in zip(holder, value)}
    else:
        other_info = [v.text.strip() for v in value_ele]
        
    return other_info

def download_image(image_url, save_path, image_name, image_ext, referer=None):
    header =None
    tries = 0
    if referer:
        header = {
            'referer'   : referer,
            'sec-ch-ua' : '"Chromium";v="137", "Google Chrome";v="137", "Not.A/Brand";v="24"',
            'sec-ch-ua-mobile' : '?0',
            'sec-ch-ua-platform' : '"macOS"',
            'user-agent' : 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137c.0.0.0 Safari/537.36'
        }
    try:
        response = requests.get(image_url, headers=header) if header else requests.get(image_url)
        if response.status_code == 200:
            if not os.path.exists(save_path):
                os.makedirs(save_path)
            if not os.path.exists(f"{save_path}/{image_name}.{image_ext}"):
                with open(os.path.join(save_path, f"{image_name}.{image_ext}"), 'wb') as f:
                    f.write(response.content)
                return True
            else:
                print(f"Image {image_name}.{image_ext} already exists. Skipping download.")
                return True
        else:
            print(f"Failed to download image from {image_url}. Status code: {response.status_code}")
            # print(f"Response content: {response.content}")
            print("Retrying...")
            tries += 1
            if tries < 5:
                time.sleep(2)
                return download_image(image_url, save_path, image_name, image_ext, referer)
    except Exception as e:
        print(f"Error downloading image from {image_url}: {e}")
        print("Retrying...")
        tries += 1
        if tries < 5:
            time.sleep(2)
            return download_image(image_url, save_path, image_name, image_ext, referer)
    return False
        
    # response = requests.get(image_url, headers=header) if header else requests.get(image_url)
    # if response.status_code == 200:
    #     with open(os.path.join(save_path, f"{image_name}.{image_ext}"), 'wb') as f:
    #         f.write(response.content)
    #     return True
    # return False

def get_chapter_list(driver, html_content, **chap_list_args):
    show_more = chap_list_args.pop('show_more', None)
    if show_more and driver:
        try:
            while True:
                more_button = WebDriverWait(driver, 5).until(
                    lambda d: d.find_element(By(**show_more))
                )
                more_button.click()
                time.sleep(1)
        except:
            pass
    if driver:
        WebDriverWait(driver, 10).until(
            lambda d: d.find_element(By(**chap_list_args))
        )
        html_content = driver.page_source
    soup = BeautifulSoup(html_content, 'html.parser')
    chap_list_ele = soup.find(**chap_list_args)
    chapter_list = [chap_ele['href'] for chap_ele in chap_list_ele.find_all('a', href=True)]
    return chapter_list

def get_chapter_name(html_content, **chap_name_args):
    soup = BeautifulSoup(html_content, 'html.parser')
    chap_name_ele = soup.find(**chap_name_args)
    if chap_name_ele:
        return chap_name_ele.text.strip()
    return ""

def get_chapter_index(something_contain_index, regex_pattern=r'(\d+\.?\d*)'):
    match = re.search(regex_pattern, str(something_contain_index))
    if match:
        return match.group(1)
    return ""

def get_image_urls(html_content, **image_list_args):
    soup = BeautifulSoup(html_content, 'html.parser')
    url_ele = image_list_args.pop('url', None)['url']
    img_ele = soup.find_all(**image_list_args)
    image_list = [get_img_url(img, url_ele) for img in img_ele]
    return image_list

def get_img_url(img_ele, url_ele):
    try:
        img_url = img_ele[url_ele]
    except:
        # using regex to find url in the element
        img_str = str(img_ele)
        pattern = r'(?!src\s*=)(\w+)\s*[=(]\s*["\']([^"\']+)["\']'
        matches = re.findall(pattern, img_str)
        for attr, val in matches:
            if attr == url_ele:
                img_url = val
                break
        else:
            print("Image URL not found.")
            img_url = ""
    return img_url
            
        