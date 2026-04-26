import requests
from bs4 import BeautifulSoup
import re
from PIL import Image
from PIL import ImageFile
import zipfile
import os
import time
import math
import tqdm

import json

# from ebooklib import epub
# from epub_style import epub_style_css, epub_cover_xhtml, epub_chapter_xhtml
# from ebook.epub import create_epub
# from utils import *
from utils.novel import *



## SITE CAN BE CRAWLED USING DOMAIN EXTRACTION METHOD OF TRUYENQQ ##

# # cover:
# 	- class: series-cover."content img-in-ratio"
# 	- element: url (not href)

# # name:
# 	- class: series-name.tag 'a'
# 	-element: text content

# # gerne:
# 	- class: series-gerne -> all series-gerne-item
# 	- element: text content

# # info:
# 	- class: info-item:
# 		+ info_name: all "info-name"
# 		+ value: all "info-value"
# 	- element: text content

# # summary (describtion):
# 	- class: summary-content
# 	- element: everything under it

# # volumes:
# 	- all content:
# 		+ class: volume-list at-series basic-section volume-mobile gradual-mobile 
# 	- vol title:
# 		+ all class: sect-title
# 		+ element: text content
# 	- vol cover:
# 		+ class: volume-cover
# 		+ element: div.a.div.div url
# 	- vol chap links:
# 		+ class: list-chapters
# 		+ element: href

        

class DoclnCrawler:
    def __init__(self, url, output_dir, sleep_time=1000):
        self.url = url
        self.base_url = extract_base_url(url)
        self.output_dir = output_dir + "outputs/Novel/" if output_dir else "outputs/Novel/"
        self.referrer = extract_base_url(url)
        self.sleep_time = sleep_time / 1000  # Convert milliseconds to seconds

        self.title = None
        self.novel_info = {
            "title": None,
            "author": None,
            "other_info": {},
            "cover_image": None,
            "num_chapters": None,
            "description": None,
            "genres": [],
            "novel_url": self.url,
            "start_chapter": None,
            "end_chapter": None,
            "chapter_links": [],
            }
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)

    def get_all_info(self, custom_volume_list=None, info_url=None):
        # custom_volume_list: list of volume links to crawl instead of extracting from page
        if custom_volume_list and info_url:
            self.url = info_url
        html_content = get_page_content(self.url)
        if not html_content:
            print("Failed to retrieve page content.")
            return



        self.title = get_title(html_content, tag='span', class_name='series-name', attr=None)
        self.output_dir = os.path.join(self.output_dir, self.title)
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        self.novel_info['title'] = self.title

        cover_image_url = get_cover_image_element(html_content,
                                                  class_name='series-cover', 
                                                    img_url_tag='url', 
                                                    output_dir=self.output_dir+"/img", ext="jpg")
        self.novel_info['cover_image'] = cover_image_url

        gernes = get_gernes(html_content, class_name='series-gerne-item')
        self.novel_info['genres'] = gernes

        other_info = get_all_other_novel_info(html_content, (None, 'info-name', None), 
                                              (None, 'info-value', None))
        for key, value in other_info.items():
            if key.lower() == 'tác giả' or key.lower() == 'author':
                self.novel_info['author'] = value
            # can add more info extraction here if needed
            else:
                self.novel_info["other_info"][key] = value
                # self.novel_info[key] = value

        description = get_description(html_content, class_name='summary-content')
        self.novel_info['description'] = description

        if custom_volume_list:
            volume_links = custom_volume_list
            # volumes = [get_volume_info_from_link(link, ('span', 'volume-name', None), 
            #                                      (None, 'series-cover', None, 'url', 
            #                                         # self.output_dir+f'/volume_cover{i}'),
            #                                       os.path.join(self.output_dir, f'volume_{i+1}')), 
            #                                       (None, 'list-chapters', None)) for i, link in enumerate(volume_links)]
            volumes = []
            for i, link in enumerate(volume_links):
                vol_info = get_volume_info_from_link(self.base_url, link, 
                                                    ('span', 'volume-name', None), 
                                                    (None, 'series-cover', None, 'url', 
                                                     self.output_dir+"/img", f'volume_{i+1}_cover'), 
                                                    (None, 'list-chapters', None))
                volumes.append(vol_info)
                time.sleep(1)  # sleep between volume requests
        else:
            volumes = get_all_volume(html_content, (None, 'volume-list', None), 
                                  (None, 'sect-title', None), 
                                  (None, 'volume-cover', None, 'href'), 
                                  (None, 'list-chapters', None), 
                                  base_url=self.base_url)

        # info = {"info" : self.novel_info}
        # vol = {"volumes": volumes}

        # save all info to a json file
        # with open(os.path.join(self.output_dir, 'novel_info1.json'), 'w', encoding='utf-8') as f:
        #     json.dump({**info, **vol}, f, ensure_ascii=False, indent=4)
        # print("Novel information extracted and saved.")

        print("Got all novel information.")

        return self.novel_info, volumes
        

    def crawl_chapter_(self, chapter_url, img_output_dir=None, img_prefix=""):
        try:
            chapter_content = get_page_content(chapter_url)
        except Exception as e:
            print(f"Error fetching chapter content from {chapter_url}: {e}")
            print("Retrying...")
            for _attempt in range(5):
                time.sleep(2)  # wait before retrying
                chapter_content = get_page_content(chapter_url)
                if chapter_content:
                    break
            else:
                print(f"Failed to retrieve chapter content from {chapter_url} after retries.")
                return None
            chapter_content = get_page_content(chapter_url)
        if not chapter_content:
            print(f"Failed to retrieve chapter content from {chapter_url}")
            return None
        chapter_title = get_chapter_title(chapter_content, tag='h4', class_name='title-item', attr=None)
        chapter_body = get_chapter_content(chapter_content, id='chapter-content')
        soup = BeautifulSoup(chapter_body, 'html.parser')
        img_src = soup.find_all('img')
        img_src[-1].decompose()  # remove last img tag (ads)
        img_src[-2].decompose()  # remove second last img tag (ads)
        chapter_body = soup.prettify()
        # re.sub(rf'{img_src[-1]}', '', chapter_body)
        # re.sub(rf'{img_src[-2]}', '', chapter_body)
        if len(img_src) > 2:
            new_chapter_body, chapter_img_folder = make_chap_img_local(chapter_body, 
                                                                   img_output_dir, img_prefix=img_prefix, ext="jpg")
        else:
            new_chapter_body = chapter_body
            chapter_img_folder = None
        
        return {
            "chapter_title": chapter_title,
            "chapter_content": new_chapter_body,
            "chapter_img_folder": chapter_img_folder
        }
    

    def crawl(self, custom_volume_list=None, info_url=None):
        novel_info, volumes = self.get_all_info(custom_volume_list=custom_volume_list, info_url=info_url)
        for idx, vol in enumerate(volumes):
            all_chapters = []
            print(f"Crawling Volume {idx+1}: {vol['title']}")
            # vol_title = vol['title']
            # vol_dir = os.path.join(self.output_dir, f"Volume_{idx+1}")
            # vol_img_dir = os.path.join(self.output_dir, 'img', f"Volume_{idx+1}")
            # if not os.path.exists(vol_img_dir):
            #     os.makedirs(vol_img_dir)

            if not custom_volume_list:
                cover_page = get_page_content(vol['cover_link'])
                vol_cover_image_url = get_cover_image_element(cover_page, class_name='series-cover',
                                                              img_url_tag='url', 
                                                              output_dir=self.output_dir, ext="jpg",
                                                              name=f"Volume_{idx+1}_cover")
            else:
                vol_cover_image_url = vol['cover_link']
            vol['cover_image'] = vol_cover_image_url
            for jdx, chap_url in tqdm.tqdm(enumerate(vol['chapter_links']),
                                           total=len(vol['chapter_links']),
                                           desc=f"Crawling Volume {idx+1} Chapters", unit="chapters"):
                # print(f"Crawling Volume {idx+1} - Chapter {jdx+1}: {chap_url}")
                chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
                                                  img_prefix=f"vol{idx+1}_chap{jdx+1}")
                all_chapters.append(chapter_data) if chapter_data else {}
                time.sleep(self.sleep_time)
            vol['chapter_contents'] = all_chapters

        info = {"info" : novel_info}
        all_vol = {"volumes": volumes}

        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
        print("Crawling completed and data saved.")


    def crawl_range(self, start_chapter, end_chapter, custom_volume_list=None, info_url=None):
        novel_info, volumes = self.get_all_info(custom_volume_list=custom_volume_list, info_url=info_url)
        flattened_chapter_links = []
        chapter_map = []  # To keep track of which chapter belongs to which volume
        for vol_idx, vol in enumerate(volumes):
            for chap_url in vol['chapter_links']:
                flattened_chapter_links.append(chap_url)
                chapter_map.append(vol_idx)
        selected_chapter_links = flattened_chapter_links[start_chapter-1:end_chapter-1]
        all_chapters = []
        for i in tqdm.tqdm(range(len(selected_chapter_links)), desc="Crawling Selected Chapters", unit="chapters"):
            chap_url = selected_chapter_links[i]
            vol_idx = chapter_map[start_chapter - 1 + i]
            chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
                                              img_prefix=f"vol{vol_idx+1}_chap{start_chapter + i}")
            all_chapters.append(chapter_data) if chapter_data else {}
            time.sleep(self.sleep_time)

        vol_0 = {
            "title": f"Chapters {start_chapter} to {end_chapter}",
            "cover_image": None,
            "chapter_links": selected_chapter_links,
            "chapter_contents": all_chapters
        }
        info = {"info" : novel_info}
        all_vol = {"volumes": [vol_0]}
        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
        print("Crawling of selected chapter range completed and data saved.")


    def crawl_chapter(self, chapter_url, info_url=None):
        if info_url:
            self.url = info_url
        novel_info, volumes = self.get_all_info(info_url=self.url)
        chapter_data = self.crawl_chapter_(chapter_url, img_output_dir=self.output_dir+"/img", 
                                          img_prefix="single_chap")
        vol_0 = {
            "title": "Single Chapter Crawl",
            "cover_image": None,
            "chapter_links": [chapter_url],
            "chapter_contents": [chapter_data] if chapter_data else []
        }
        info = {"info" : novel_info}
        all_vol = {"volumes": [vol_0]}
        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
        print("Crawling of single chapter completed and data saved.")


    def make_epub(self):
        # self.output_dir = os.path.join(self.output_dir, "Hội chứng muốn sống bình an tại dị giới")
        novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
        if not os.path.exists(novel_info_path):
            print("Novel information file not found. Please run the crawler first.")
            return
        with open(novel_info_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        novel_info = data['info']
        volumes = data['volumes']

        # make volumes epub elements
        volume_list = []
        for vol in volumes:
            print(f"Preparing Volume: {vol['title']}")
            chapter_elements = []
            for idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
                                     desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
                c = make_chapter_epub(chapter, title_added=("", ""), idx=idx+1)
                chapter_elements.append(c)
            v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
            volume_list.append((v_img, v, c_lst))
        # create epub
        make_book_epub(novel_info, volume_list, ("",""), output_path=self.output_dir, ebook_name=novel_info['title'] + ".epub")



    def make_volume_epub(self):
        novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
        if not os.path.exists(novel_info_path):
            print("Novel information file not found. Please run the crawler first.")
            return
        with open(novel_info_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        novel_info = data['info']
        volumes = data['volumes']

        for vol in volumes:
            print(f"Preparing Volume: {vol['title']}")
            chapter_elements = []
            for idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
                                     desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
                c = make_chapter_epub(chapter, title_added=("", ""), idx=idx+1)
                chapter_elements.append(c)
            v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
            # create epub for each volume
            make_book_epub(novel_info, [(v_img, v, c_lst)], ("",""), output_path=self.output_dir, ebook_name=novel_info['title'] + f" - {vol['title']}.epub")




def test():
    url = "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun"

    vol_lst1 = ["https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t17045-arc-2-vach-tran"]
    vol_lst = [
        "https://docln.net/truyen/8306-isekai-demo-bunan-ni-ikitai-shoukougun/t12965-arc-1",
        "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t17045-arc-2-vach-tran",
        "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t18598-arc-3-tao-ngo",
        "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t19121-arc-4-ke-sach",
        "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t20030-arc-5-khai-mac",
        "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t20803-arc-6-kich-chien",
        "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t21609-arc-7-lua-chon",
        "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t22375-arc-8-cham-dut",
        "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t23615-arc-9-binh-an",
        "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t21555-ngoai-truyen"
    ]



    crawler = DoclnCrawler(url, output_dir=None, sleep_time=1000)
    # crawler.get_all_info()
    crawler.crawl(custom_volume_list=vol_lst, info_url=url)
    # crawler.crawl_chapter(chap2)
    print("output dir:", crawler.output_dir)

    crawler.make_epub()



def docln_crawler_control(crawler, action, *args, compile_type="single_volume"):

    if action == "get_all":
        crawler.crawl(*args)
    elif action == "get_chapter_range":
        crawler.crawl_range(*args)
    elif action == "get_chapter":
        crawler.crawl_chapter(*args)
    else:
        raise ValueError(f"Unknown action: {action}")
    
    if compile_type == "single_volume":
        crawler.make_epub()
    elif compile_type == "multi_volume":
        crawler.make_volume_epub()

    return
    


if __name__ == "__main__":
    # test()
    pass
    # chap2 = "https://docln.net/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/c93334-chuong-02-truoc-mat-toi-da-ky-vong-mot-chut"
    # output_dir = "outputs"
    # base_url = extract_base_url(url)
    # print("Base URL:", base_url)
    # # Expected output: "https://docln.net/"
    # html_content = get_page_content(url)
    # if html_content:
    #     # save html_content to a file or process further
    #     with open("sample_docln_page.html", "w", encoding="utf-8") as file:
    #         file.write(html_content)
    #     title = get_title(html_content, tag='span', class_name='series-name', attr=None)
    #     print("Title:", title)
    # # test_content = """<div class='series-cover content img-in-ratio'> some text </div>
    # # <img src='https://docln.sbs/images/novel/11662/cover.jpg' alt='Cover Image'/>"""
    # # test_html = BeautifulSoup(test_content, 'html.parser')
    # # print("Test HTML:", test_html.prettify())
    # cover_image_url = get_cover_image_element(html_content,
    #                                               class_name='series-cover', 
    #                                                 img_url_tag='url', 
    #                                                 output_dir=output_dir, ext="jpg")
    # # cover_image_element = get_cover_image_element(test_content,
    # #                                               tag='img',
    # #                                               class_name=None, 
    # #                                               attr=None,
    # #                                               img_url_tag='src')
    # print("Cover Image element:", cover_image_url)

    # gernes = get_gernes(html_content, class_name='series-gerne-item')
    # print("Genres found:", gernes)

    # other_info = get_all_other_novel_info(html_content, (None, 'info-name', None), 
    #                                       (None, 'info-value', None))
    # print("Other Novel Info:", other_info)

    # description = get_description(html_content, class_name='summary-content')
    # print("Description found:", [description])

    # volumes = get_volume(html_content, (None, 'volume-list', None), 
    #                           (None, 'sect-title', None), 
    #                           (None, 'volume-cover', None, 'href'), 
    #                           (None, 'list-chapters', None), 
    #                           base_url=base_url)


    # print("Crawler initialized for URL:", url)
    # # else:
    # #     print("Failed to retrieve page content.")
    # #     print("Title: None")
