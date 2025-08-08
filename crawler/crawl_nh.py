import requests
from bs4 import BeautifulSoup
import re
import os
import time
from selenium import webdriver
import tqdm

from ebook.cbz_converter import cbz2pdf
from ebook.make_pdf import create_pdf
from ebook.make_cbz import create_cbz

import zipfile
from PIL import Image, ImageFile

class crawl_nh:
    def __init__(self, url, output_dir=None):
        self.base_url = "https://nhentai.net"
        if url.isdigit():
            print(f"Detected URL as CODE: {url}")
            # If the input is a number, treat it as a CODE
            self.url = f"{self.base_url}/g/{url}/"
        else:
            print(f"Detected URL as full URL: {url}")
            # Otherwise, treat it as a full URL
            self.url = url
        # self.referrer = "https://truyenqqgo.com/"
        self.comic_name = None
        self.path = "outputs" if output_dir is None else output_dir
        

    
    def crawl(self):
        print(f"Starting to crawl {self.url}...")
        headers = {
            # "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
            # "User-Agent": "Mozilla/5.0"
        }
        response = requests.get(self.url, headers=headers)
        rec = 0
        while response.status_code != 200 and rec < 5:
        # if response.status_code != 200 and rec != 5:
            # print(f"status code: {response.status_code}")
            # raise Exception(f"Failed to fetch the page: {response.status_code}")
            print(f"Failed to fetch the page: {response.status_code}, retrying...")
            time.sleep(0.2)
            rec += 1
            response = requests.get(self.url, headers=headers)

        if response.status_code != 200:
            print(f"Failed to fetch the page after 5 retries: {response.status_code}, trying with Selenium...")
            # If the request fails, use Selenium to fetch the page
            web = webdriver.Chrome()
            web.get(self.url)
            time.sleep(2)  # Wait for the page to load
            content = web.page_source
            web.quit()
        else:
            content = response.content
        
        soup = BeautifulSoup(content, 'html.parser')
        
        # Extract comic name
        self.comic_name = soup.find('h1', class_='title').text.strip()
        # clean up the comic name
        self.comic_name = re.sub(r'[\\/:*?"<>|]', '', self.comic_name)
        self.comic_name = re.sub(r'\s+', ' ', self.comic_name)
        # Truncate comic name if it exceeds 300 characters
        if len(self.comic_name) > 300:
            print("Comic name is too long, truncating to 200 characters.")
            pattern = r'\[([^\]]+)\]'  # Matches content within square brackets
            title = re.sub(pattern, '', self.comic_name).strip()
            
            # Clean up multiple spaces
            self.comic_name = re.sub(r'\s+', ' ', title)
        if len(self.comic_name) > 300:
            self.comic_name = self.comic_name[:200]
        print(f"Comic name: {self.comic_name}")
        
        # Create output directory for the comic
        print(f"path before: {self.path}")
        self.path = os.path.join(self.path, self.comic_name)
        print(f"path after: {self.path}")
        if not os.path.exists(self.path):
            print(f"Creating directory: {self.path}")
            os.makedirs(self.path)
        
        # Extract pages
        pages = soup.find_all('div', class_='thumb-container')
        print(f"Found {len(pages)} pages.")
        
        for i, page in enumerate(tqdm.tqdm(pages, desc="Downloading pages")):
            img_url = page.find('a')['href']
            img_url = self.base_url + img_url
            print(f"Image URL {i+1}: {img_url}")
            # img_url = self.url + str(i+1)
            # if not img_url.startswith("http"):
            #     img_url = "https:" + img_url
            img_response = requests.get(img_url, headers=headers)
            if img_response.status_code != 200:
                print(f"Failed to fetch image {i+1}: {img_response.status_code}")
                continue
            img_page = BeautifulSoup(img_response.content, 'html.parser')
            img_src = img_page.find(id='image-container').find('img')['src']
            print(f"Image source {i+1}: {img_src}")
            if not img_src.startswith("http"):
                img_src = "https:" + img_src
            img = requests.get(img_src, headers=headers)
            if img.status_code != 200:
                print(f"Failed to fetch image source {i}: {img.status_code}")
                continue
            else:
                img_name = f"{i}.jpg"
                img_path = os.path.join(self.path, img_name)
                with open(img_path, 'wb') as f:
                    f.write(img.content)
                # print(f"Downloaded page {i+1}: {img_name}")
        
        print("Crawling completed.")
        
    def create_cbz(self, delete_chapter_dir=True):
        """
        Create cbz from images
        """
        if not os.path.exists(self.path):
            print(f"No images found for comic {self.comic_name}")
            return "error"
        
        img_ext = [".jpg", ".jpeg", ".png", ".gif"]
        fnames = [i for i in os.listdir(self.path) if os.path.splitext(i)[1].lower() in img_ext]
        
        # Sort filenames by number in filename
        fnames.sort(key=lambda x: int(re.search(r'\d+', x).group()))
        
        if not fnames:
            print(f"No images found in {self.path}")
            return "error"
        # Create cbz from images
        try:
            cbz_path = os.path.join(self.path, f"{self.comic_name}.cbz")
            with zipfile.ZipFile(cbz_path, "w") as cbz:
                for img in fnames:
                    cbz.write(os.path.join(self.path, img), img)
            print(f"Saved chapter {self.comic_name} as CBZ.")
            
        except Exception as e:
            print("Error creating cbz: ", e)
            return "error"
        
        if delete_chapter_dir:
            # Remove image directory after creating cbz
            print(f"Removing chapter {self.comic_name} directory after creating cbz")
            for img in fnames:
                os.remove(os.path.join(self.path, img))
            print(f"Removed chapter {self.comic_name} directory after creating cbz")
        return "cbz created"
        
        
    def create_pdf(self, delete_chapter_dir=True):
        """
        Create PDF from images
        """
        if not os.path.exists(self.path):
            print(f"No images found for comic {self.comic_name}")
            return "error"
        
        img_ext = [".jpg", ".jpeg", ".png", ".gif"]
        fnames = [i for i in os.listdir(self.path) if os.path.splitext(i)[1].lower() in img_ext]
        
        # Sort filenames by number in filename
        fnames.sort(key=lambda x: int(re.search(r'\d+', x).group()))
        
        if not fnames:
            print(f"No images found in {self.path}")
            return "error"
        
        pdf_path = os.path.join(self.path, f"{self.comic_name}.pdf")
        try:
            imgs = [Image.open(os.path.join(self.path, i)) for i in fnames]
            ImageFile.LOAD_TRUNCATED_IMAGES = True  # allow truncated images
            imgs[0].save(pdf_path, "PDF", save_all=True, append_images=imgs[1:], resolution=100.0)
        except Exception as e:
            print("Error creating pdf: ", e)
            return "error"
        
        if delete_chapter_dir:
            # Remove image directory after creating pdf
            print(f"Removing chapter {self.comic_name} directory after creating pdf")
            for img in fnames:
                os.remove(os.path.join(self.path, img))
            print(f"Removed chapter {self.comic_name} directory after creating pdf")
        
        print(f"Saved chapter {self.comic_name} as PDF.")
        return "pdf created"
    
    
def nh_control():
    url = input("Enter the URL or the CODE of the nhentai: ")
    output_dir = input("Enter the output directory (default is 'outputs'): ") or "outputs"
    crawler = crawl_nh(url, output_dir)
    
    try:
        crawler.crawl()
        print("Crawling completed successfully.")
        
        action = input("Choose an action (cbz/pdf) or none if you only want pictures: ").strip().lower()
        if action == "cbz":
            delete_chapter_dir = input("Delete chapter directory after creating CBZ/PDF? (y/n): ").strip().lower() == "y"
            result = crawler.create_cbz(delete_chapter_dir=delete_chapter_dir)
            print(result)
        elif action == "pdf":
            delete_chapter_dir = input("Delete chapter directory after creating CBZ/PDF? (y/n): ").strip().lower() == "y"
            result = crawler.create_pdf(delete_chapter_dir=delete_chapter_dir)
            print(result)
        elif action == "none":
            print("No action taken. Pictures are saved in the output directory.")
        else:
            print("Invalid action. Please try again.")
    except Exception as e:
        print(f"An error occurred during crawling: {e}")