import os
import time

import re
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

import pandas as pd

import json
from ebooklib import epub
import tqdm
import mimetypes
# from epub_style import epub_style_css, epub_cover_xhtml, epub_chapter_xhtml



def get_page_content(url, headers=None):
    """
    Fetches the HTML content of a given URL.
    Args:
        url (str): The URL of the page to fetch.
        headers (dict, optional): Additional headers to include in the request.
    Returns:
        str: The HTML content of the page, or None if the request fails.
    """
    # driver = webdriver.Chrome()  # Ensure you have the ChromeDriver installed and in PATH
    # driver.set_page_load_timeout(10) # Set timeout to 30 seconds
    # driver.implicitly_wait(5)  # Wait up to 5 seconds for elements to load
    html_content = requests.get(url)

    try:
        # driver.get(url)
        time.sleep(2)  # Wait for the page to load
        # soup = BeautifulSoup(driver.page_source, 'html.parser')
        soup = BeautifulSoup(html_content.text, 'html.parser')
        html_content = soup.prettify()
        print("Page content:", html_content)
        return html_content

    except Exception as e:
        print(f"Error fetching page content: {e}")
        return None


get_page_content("https://valvrareteam.net/truyen/no-game-no-life-cd23c8d9")