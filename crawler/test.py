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
import pytesseract

import time
import os
import json

def get_user_config():
    json_path = os.path.join(os.path.dirname(__file__), 'config.json')
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Configuration file not found: {json_path}")
    
    with open(json_path, 'r', encoding='utf-8') as file:
        config = json.load(file)
    return config

def write_user_config(config):
    json_path = os.path.join(os.path.dirname(__file__), 'config.json')
    with open(json_path, 'w', encoding='utf-8') as file:
        json.dump(config, file, indent=4)



def get_page_source(url):
    # Initialize the Chrome driver
    driver = webdriver.Chrome()
    
    try:
        # Open the URL
        driver.get(url)
        
        # Wait for the page to load completely
        time.sleep(5)  # Adjust this sleep time as necessary
        
        # Get the page source
        page_source = driver.page_source
        
        return page_source
    finally:
        driver.quit()