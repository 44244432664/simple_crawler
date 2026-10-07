import requests
from bs4 import BeautifulSoup
import re
import json
import os
import time

import tqdm


from epub_style import epub_style_css, epub_cover_xhtml, epub_chapter_xhtml
from ebook.epub import create_epub


class crawl_xx:
    def __init__(self, url, output_dir="outputs"):
        self.url = url
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }
        self.output_dir = output_dir
        self.novel_info = {}

    def crawl(self):
        response = requests.get(self.url, headers=self.headers)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch the page: {response.status_code}")
        
        soup = BeautifulSoup(response.content, 'html.parser')
        self._parse_novel_info(soup)
        self._parse_chapters(soup)
        self.save_novel_info(self.output_dir)
        self.create_epub(self.output_dir)

    def _parse_novel_info(self, soup):
        # Implement logic to parse novel information from the soup object
        info = soup.find_all('tr')
        total_chapters = len(soup.find_all('div', class_='bai-viet-box')[1].find_all('a')) + 1
        self.novel_info = {
            "title" : info[1].find_all('td')[1].text.strip() if len(info) > 1 else "Unknown Title",
            "author": info[2].find_all('td')[1].text.strip() if len(info) > 2 else "Unknown Author", 
            "genres": [genre.text.strip() for genre in info[4].find_all('td')[1].find_all('a')] if len(info) > 3 else [],
            "status": info[5].find_all('td')[1].text.strip() if len(info) > 3 else "Unknown Status",
            "num_chapters": total_chapters,
            "novel_url": self.url,
            "chapters": []
        }
        os.makedirs(f'{self.output_dir}/{self.novel_info["title"]}', exist_ok=True)
        # with open(os.path.join(f'{self.output_dir}/{self.novel_info["title"]}', 'novel_info.json'), 'w', encoding='utf-8') as f:
        #     json.dump(self.novel_info, f, ensure_ascii=True, indent=4)
        # print(f"Novel Title: {self.novel_info['title']}")

    def _parse_chapters(self, soup):
        # Implement logic to parse chapters from the soup object
        for i in tqdm.tqdm(range(1, self.novel_info['num_chapters']+1), desc="Parsing chapters"):
            chapter_url = f"{self.url}/{i}/"
            response = requests.get(chapter_url, headers=self.headers)
            if response.status_code != 200:
                print(f"Failed to fetch chapter {i}: {response.status_code}")
                continue
            
            chapter_soup = BeautifulSoup(response.content, 'html.parser')
            full_content = chapter_soup.find('div', class_='ndtruyen')
            for im in full_content.find_all('img'):
                im.decompose()
            title = chapter_soup.find('title').text.strip() if chapter_soup.find('title') else f"Chương {i}"
            
            self.novel_info["chapters"].append({
                'index': i,
                'title': title,
                'content': str(full_content.prettify())
            })
            time.sleep(0.2)  # Be polite and avoid overwhelming the server
        
        # with open(os.path.join(f'{self.output_dir}/{self.novel_info["title"]}', 'novel_info.json'), 'w', encoding='utf-8') as f:
        #     json.dump(self.novel_info, f, ensure_ascii=True, indent=4)
            
    def save_novel_info(self, output_dir):
        save_dir = os.path.join(output_dir, self.novel_info['title'])
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        
        with open(os.path.join(save_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump(self.novel_info, f, ensure_ascii=True, indent=4)
            
        print(f"Novel information saved to {os.path.join(save_dir, 'novel_info.json')}")
        
    def create_epub(self, output_dir):
        save_dir = os.path.join(output_dir, self.novel_info['title'])
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        
        # epub_file = os.path.join(save_dir, f"{self.novel_info['title']}.epub")
        ebook_path = create_epub(
            json_file=os.path.join(save_dir, 'novel_info.json'),
            output_path=save_dir,
            chapter_num=None  # Include all chapters
        )
        print(f"EPUB file created at {ebook_path}")
        return ebook_path
    
def xx_control():
    url = input("Enter the URL of the XX novel: ")
    output_dir = input("Enter the output directory (default is 'outputs'): ") or "outputs"
    crawler = crawl_xx(url, output_dir)
    try:
        crawler.crawl()
        print("Crawling completed successfully.")
    except Exception as e:
        print(f"An error occurred during crawling: {e}")
    
    