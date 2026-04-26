from zipfile import ZipFile
import pandas as pd
import json

from utils.comic import *

class ComicCrawler:
    def __init__(self, comic_url, selenium_driver=False):
        self.comic_url = comic_url
        self.base_url = extract_base_url(comic_url)
        self.referrer = self.base_url
        self.driver = None
        if selenium_driver:
            self.driver = webdriver.Chrome()

        data = pd.read_csv("data/alias.csv")
        site = self.base_url.split("//")[-1].split("/")[0]
        self.site_name = data[data['site'] == site]['name'].values[0]
        with open(f"data/formats/{self.site_name}.json", 'r', encoding='utf-8') as f:
            self.format_data = json.load(f)


    def comic_info(self, html_content, **info_ele_args):
        title_args = self.format_data.get('title', {})
        genre_args = self.format_data.get('genre', {})
        description_args = self.format_data.get('description', {})
        other_info_args = self.format_data.get('other_info', {})
        title = get_title(html_content, **title_args)
        genre = get_genre(html_content, **genre_args)
        other_info = get_other_info(html_content, **other_info_args)
        description = get_description(html_content, **description_args)
        return {
            'title'         : title,
            'genre'         : genre,
            'description'   : description,
            'other_info'    : other_info    # when holder is absent, other_info is a list
        }

    def chapter_url_list(self, html_content, **chap_list_args):
        chapter_urls = get_chapter_list(self.driver, html_content, **chap_list_args)
        return {'chapter_urls': chapter_urls}
    

    def get_chapter_data(self, chapter_html, **chapter_data_args):
        html = BeautifulSoup(chapter_html, 'html.parser')

        chap_num_args = chapter_data_args.get('chapter_number', {})
        chapter_index = chapter_data_args.get('chapter_index', -1)
        # anything that can be converted to string
        # called something_contain_index
        # and the regex_pattern is applied to extract the index number
        image_list_args = chapter_data_args.get('image_list', {})

        chap_num = get_chapter_name(chapter_html, **chap_num_args)
        
        num = get_chapter_index(chap_num)
        image_list = get_image_urls(html, **image_list_args)

        return {
            'chapter_index' : chapter_index,
            'chapter_number': num,
            'image_list'    : image_list
        }
    
    def crawl_chapter_(self, chapter_html, save_path, img_ext='jpg'):
        chapter_data = self.get_chapter_data(chapter_html, **self.format_data.get('chapter_data', {}))
        chapter_path = os.path.join(save_path, f"chap_{chapter_data['chapter_index']}")
        for i in range(len(chapter_data['image_list'])):
            image_url = chapter_data['image_list'][i]
            image_name = f"img_{i+1}"
            downloaded = download_image(image_url, chapter_path, image_name, img_ext)
            if downloaded:
                print(f"✓ Downloaded: {image_name}.{img_ext}")
            else:
                print(f"✗ Failed: {image_name}.{img_ext}")
        return chapter_data
    
    def crawl(self, url, save_path, img_ext='jpg'):
        # TODO: make it similar crawl() in NovelCrawler and run like crawl_qq
        if self.driver:
            self.driver.get(url)
            time.sleep(2)
            html_content = self.driver.page_source
        else:
            response = requests.get(url)
            html_content = response.text
        comic_info = self.comic_info(html_content, **self.format_data.get('comic_info', {}))
        chapter_urls = self.chapter_url_list(html_content, **self.format_data.get('chapter_list', {}))['chapter_urls']
        all_chapter_data = []
        for idx, chap_url in enumerate(chapter_urls):
            print(f"Crawling chapter {idx+1}/{len(chapter_urls)}: {chap_url}")
            if self.driver:
                self.driver.get(chap_url)
                time.sleep(2)
                chap_html = self.driver.page_source
            else:
                response = requests.get(chap_url)
                chap_html = response.text
            chapter_data = self.crawl_chapter_(chap_html, save_path, img_ext)
            all_chapter_data.append(chapter_data)
        return {
            'comic_info'        : comic_info,
            'chapters_data'     : all_chapter_data
        }
        

    def make_cbz(self, save_path):
        # TODO: find a way to use create_cbz from ebook/make_cbz.py
        # iterate through all chapter folders in save_path
        for folder_name in tqdm.tqdm(os.listdir(save_path), desc="Creating CBZ files", unit="chapter"):
            folder_path = os.path.join(save_path, folder_name)
            if os.path.isdir(folder_path):
                cbz_name = f"{folder_name}.cbz"
                cbz_path = os.path.join(save_path, cbz_name)
                with ZipFile(cbz_path, 'w') as cbz_file:
                    for img_name in sorted(os.listdir(folder_path)):
                        img_path = os.path.join(folder_path, img_name)
                        cbz_file.write(img_path, arcname=img_name)
                # print(f"Created CBZ: {cbz_name}")

    def make_pdf(self, save_path):
        # TODO: create pdf from images in each chapter folder
        # iterate through all chapter folders in save_path
        for folder_name in tqdm.tqdm(os.listdir(save_path), desc="Creating PDF files", unit="chapter"):
            folder_path = os.path.join(save_path, folder_name)
            if os.path.isdir(folder_path):
                pdf_name = f"{folder_name}.pdf"
                pdf_path = os.path.join(save_path, pdf_name)
                image_list = []
                for img_name in sorted(os.listdir(folder_path)):
                    img_path = os.path.join(folder_path, img_name)
                    image = Image.open(img_path).convert('RGB')
                    image_list.append(image)
                if image_list:
                    first_image = image_list.pop(0)
                    first_image.save(pdf_path, save_all=True, append_images=image_list)
                # print(f"Created PDF: {pdf_name}")

            

    def close(self):
        if self.driver:
            self.driver.quit()


def run(comic_url, save_path, img_ext='jpg', make_cbz=False, make_pdf=False, selenium_driver=False):
    crawler = ComicCrawler(comic_url, selenium_driver)
    crawl_data = crawler.crawl(comic_url, save_path, img_ext)
    if make_cbz:
        crawler.make_cbz(save_path)
    if make_pdf:
        crawler.make_pdf(save_path)
    crawler.close()
    return crawl_data