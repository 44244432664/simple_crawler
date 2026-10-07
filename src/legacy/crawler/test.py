# from selenium import webdriver
# from selenium.webdriver.common.by import By
# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC
# from selenium.webdriver.chrome.options import Options


# from bs4 import BeautifulSoup

# def crawl_vue_page(url):
#     # Configure Chrome options
#     chrome_options = Options()
#     chrome_options.add_argument("--headless")  # Run in background
#     chrome_options.add_argument("--no-sandbox")
#     chrome_options.add_argument("--disable-dev-shm-usage")
    
#     # Initialize driver
#     driver = webdriver.Chrome(options=chrome_options)
    
#     try:
#         driver.get(url)
        
#         # Wait for Vue.js to load content
#         wait = WebDriverWait(driver, 10)
        
#         # Wait for specific element or use a general wait
#         # Example: wait for an element with class 'vue-content'
#         # wait.until(EC.presence_of_element_located((By.CLASS_NAME, "canvas")))
#         # general wait for the page to be fully loaded
#         # This is a simple check, you can modify it to wait for specific elements
#         # wait.until(lambda driver: driver.execute_script("return document.readyState") == "complete
        
#         # Or wait for Vue to be ready
#         driver.execute_script("return document.readyState") == "complete"
        
#         # Extract content
#         page_source = driver.page_source
        
#         # You can also target specific elements
#         # elements = driver.find_elements(By.TAG_NAME, "div")
        
#         # return page_source
        
#         content = BeautifulSoup(page_source, 'html.parser')
        
#         with open("content.html", "w", encoding="utf-8") as file:
#             file.write(content.prettify())
        
#     finally:
#         driver.quit()

# # Usage
# url = "https://cuutruyen.net/mangas/527/chapters/6893"
# content = crawl_vue_page(url)



from bs4 import BeautifulSoup
from selenium import webdriver
from PIL import Image
# import pytesseract
import requests
from selenium import webdriver
from tqdm import tqdm

import time
import os
import json

base_url = "https://nhentai.net"
nh_url = "https://nhentai.net/g/587488/"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
}

# proxies = { 
#               "http"  : "172.168.1.123:80", 
#               "https" : "172.168.1.123:80", 
#             }
# response = requests.get(nh_url, headers=headers)
# print(f"Response status code: {response.status_code}")

web = webdriver.Chrome()
web.get(nh_url)
# time.sleep(2)  # Wait for the page to load

soup = BeautifulSoup(web.page_source, 'html.parser')
# soup = BeautifulSoup(response.content, 'html.parser')
web.quit()

pages = soup.find_all('div', class_='thumb-container')
print(f"Found {len(pages)} pages.")

img_links = []

img_types = ['jpg', 'png', 'gif', 'jpeg', 'webp']

for i, page in enumerate(pages):
    img_url = page.find('img')['src']
    if "nhentai.net" not in img_url:
        img_url = page.find('img')['data-src']
    if not img_url.startswith("http"):
        img_url = "https:" + img_url
    print(f"\nImage URL {i+1}: {img_url}")
    try:
        img_response = requests.get(img_url, headers=headers)
        if img_response.status_code != 200:
            print(f"Failed to fetch image {i+1}: {img_response.status_code}")
            continue
        img_links.append(img_url)

    except requests.RequestException as e:
        print(f"Error fetching image {i+1}: {e}")
        continue

    time.sleep(0.2)  # Sleep to avoid overwhelming the server

print(f"Total images found: {len(img_links)}")
print("Image links:")
for link in img_links:
    print(link)