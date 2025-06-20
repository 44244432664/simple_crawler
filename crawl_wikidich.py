import requests
from bs4 import BeautifulSoup
import re
import json
import os
import time

import tqdm

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

from ebooklib import epub

from epub_style import epub_style_css, epub_cover_xhtml, epub_chapter_xhtml
from ebook.epub import create_epub

class test:
    def __init__(self):
        self.url = "https://wattpad.com.vn/manh-su-tai-thuong-truyen-chu"
        self.base_url = "https://wattpad.com.vn"
        self.title = "manh su tai thuong"
        self.output_dir = f"{self.title}"
        self.novel_info = {
            "title": None,
            "author": None,
            "cover_image": None,
            "num_chapters": None,
            "description": None,
            "genres": [],
            "start_chapter": None,
            "end_chapter": None,
            "chapter_links": [],
            "chapters": []}
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        
        
    def get_page_content_(self):
        web = webdriver.Chrome()
        web.get(self.url)
        # time.sleep(2)
        soup = BeautifulSoup(web.page_source, 'html.parser')
        
        web.quit()
        with open("new_page_content.html", "w", encoding="utf-8") as file:
            file.write(soup.prettify())
        print("Page content saved to new_page_content.html")
        return "page content"
        
        
    def get_novel_info_(self):
        # This method can be implemented to extract novel information
        print("getting novel info...")
        # web = webdriver.Chrome()
        # web.get(self.url)
        respose = requests.get(self.url)
        # soup = BeautifulSoup(web.page_source, 'html.parser')
        soup = BeautifulSoup(respose.text, 'html.parser')
        # time.sleep(2)
        
        # cover_image = web.find_element(By.XPATH, "//img[@itemprop='image']").get_attribute("src")
        # title = web.find_element(By.XPATH, "//span[@itemprop='name']").text
        # author = web.find_element(By.XPATH, "//a[@itemprop='author']").text
        book_info = soup.find("div", class_="book-info")
        cover_image_link = book_info.find("img", itemprop="image").get("src")
        try:
            with requests.get(self.base_url+cover_image_link) as response:
                if response.status_code == 200:
                    with open(f"{self.output_dir}/cover.jpg", "wb") as file:
                        file.write(response.content)
                    cover_image = f"{self.output_dir}/cover.jpg"
                else:
                    print(f"Failed to retrieve cover image, status code: {response.status_code}")
                    cover_image = None
        except Exception as e:
            print(f"Error retrieving cover image: {e}")
            cover_image = None
        title = book_info.find(itemprop="name").text
        author = book_info.find(itemprop="author").text
        num_chapters = int(re.search(r'Số chương: (\d+)', soup.text).group(1))
        genres = [genre.text for genre in soup.find("li", class_="li--genres").find_all("a")]
        # genres = str(soup.find("li", class_="li--genres"))
        desc = str(soup.find("div", itemprop="description"))
        start_chapter = soup.find("a", string="Chương 1").get("href")
        end_chapter = soup.find("a", string="Chương cuối").get("href")
        # cover_image = soup.find("img", itemprop="image").get("src")
        # title = soup.find(class_="book-info").find(itemprop="name").text
        # author = soup.find("a", itemprop="author").text
        # num_chapters = int(re.search(r'Số chương: (\d+)', soup.text).group(1))
        # start_chapter = soup.find("a", string="Chương 1").get("href")
        # end_chapter = soup.find("a", string="Chương cuối").get("href")
        
        # web.quit()
        self.novel_info = {
            "title": title,
            "author": author,
            "cover_image": cover_image,
            "num_chapters": num_chapters,
            "description": desc,
            "genres": genres,
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
            "chapter_links": [],
            "chapters": []
        }
        
        # compare novel info with existing info
        if os.path.exists(f"{self.output_dir}/{self.novel_info['title']}_info.json"):
            with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "r", encoding="utf-8") as file:
                existing_info = json.load(file)
            if existing_info["title"] == self.novel_info["title"] and \
               existing_info["author"] == self.novel_info["author"] and \
                existing_info["num_chapters"] == self.novel_info["num_chapters"] and \
                existing_info["start_chapter"] == self.novel_info["start_chapter"] and \
                existing_info["end_chapter"] == self.novel_info["end_chapter"]:
                print("Novel information is the same as the existing one, using existing data.")
                self.novel_info = existing_info
                # return True
            else:
                print("Novel information is different, updating the existing data.")
                if self.novel_info["num_chapters"] > existing_info["num_chapters"]:
                    print("Novel has new chapters, updating chapter links...")
                    old_chapter_index = existing_info["chapter_links"].index(existing_info["end_chapter"]) + 1
                    chapter_url = self.base_url + existing_info["end_chapter"] if existing_info["end_chapter"].startswith("/") else existing_info["end_chapter"]
                    self.novel_info["chapter_links"] = existing_info["chapter_links"][:old_chapter_index]
                    pbar = tqdm.tqdm(total=self.novel_info["num_chapters"] - old_chapter_index, desc="Processing chapters", unit="chapter")
                    for i in range(old_chapter_index, self.novel_info["num_chapters"] + 1):
                        # if (i+1)%10 == 0:
                        #     print(f"Processing chapter {i+1}/{self.novel_info['num_chapters']}")
                        # chapter_url = self.base_url+self.novel_info["end_chapter"] if i == 0 else chapter_url
                        soup = BeautifulSoup(requests.get(chapter_url).text, 'html.parser')
                        next_chapter = soup.find("a", class_="next").get("href")
                        self.novel_info["chapter_links"].append(chapter_url)
                        chapter_url = self.base_url+next_chapter
                        pbar.update(1)
                        time.sleep(0.8)  # To avoid overwhelming the server
                    pbar.close()
                        
                if self.novel_info["start_chapter"] != existing_info["start_chapter"]:
                    print("All chapters links have changed, updating all chapter links...")
                    self.novel_info["chapter_links"] = self.get_all_chapters_links_()
                    
        else:
            print("No existing novel information found, creating new save with all chapter links data. This may take a while...")
            self.novel_info["chapter_links"] = self.get_all_chapters_links_()
        
        with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "w", encoding="utf-8") as file:
            json.dump(self.novel_info, file, ensure_ascii=True, indent=4)
        print("Novel information saved to novel_info.json")
        print(f"Chapter links is retrieved: {not (len(self.novel_info['chapter_links']) == 0)}")
        return "novel info"
    
    
    def get_all_chapters_links_(self):
        print("getting all chapters links...")
        if not self.novel_info:
            print("Novel information not found. Please run get_novel_info() first.")
            return
        chapter_links = []
        chapter_url = self.base_url + self.novel_info["start_chapter"]
        i = 0
        tries = 0
        pbar = tqdm.tqdm(total=self.novel_info["num_chapters"], desc="Processing chapters", unit="chapter")
        while i < self.novel_info["num_chapters"]:
            try:
                # if (i+1)%10 == 0:
                #     print(f"Processing chapter {i+1}/{self.novel_info['num_chapters']}")
                chapter_url = self.base_url+self.novel_info["start_chapter"] if i == 0 else chapter_url
                soup = BeautifulSoup(requests.get(chapter_url).text, 'html.parser')
                next_chapter = soup.find(class_="next").get("href")
                chapter_links.append(chapter_url)
                chapter_url = self.base_url+next_chapter if next_chapter.startswith("/") else next_chapter
                pbar.update(1)
                time.sleep(0.1)  # To avoid overwhelming the server
            except Exception as e:
                print(f"Error processing chapter {i+1}: {e}")
                print("Trying again...")
                
                if tries > 5:    
                    print("Too many errors, trying again one more time with selenium...")                
                    try:
                        web = webdriver.Chrome()
                        web.get(self.url)
                        search_box = web.find_element(By.XPATH, "//input[@placeholder='Nhập số chương...']")
                        search_box.send_keys(str(i+1))
                        search_box.send_keys(Keys.ENTER)
                        time.sleep(3)
                        if len(web.window_handles) > 1:
                            web.switch_to.window(web.window_handles[0])
                        link_rel = web.find_element(By.XPATH, "//h2[@class='current-chapter']").find_element(By.TAG_NAME, "a").get_attribute("href")
                        chapter_url = self.base_url + link_rel if not link_rel.startswith("http") else link_rel
                        web.quit()
                        print(f"Chapter {i+1} found at URL: {chapter_url}")
                        print(f"Continuing with chapter {i+1} at URL: {chapter_url}")
                        continue
                    except Exception as e:
                        print("Too many errors, saving current state and continuing to the next chapter.")
                        tries = 0
                        # Save the current state in case of an error
                        self.novel_info["chapter_links"] = chapter_links
                        with open(f"{self.output_dir}/error_log.txt", "a", encoding="utf-8") as error_file:
                            error_file.write(f"Error processing chapter {i+1}: {e}\nChapter URL: {chapter_url}\n\n")
                        # Create the file if it doesn't exist, or load existing data
                        incomplete_file_path = f"{self.output_dir}/incomplete.json"

                        if os.path.exists(incomplete_file_path):
                            with open(incomplete_file_path, "r", encoding="utf-8") as file:
                                file_data = json.load(file)
                        else:
                            file_data = {}

                        # Update the data
                        if "chapter" in file_data:
                            file_data["chapter"].append(i+1)
                        else:
                            file_data["chapter"] = [i+1]

                        if "url" in file_data:
                            file_data["url"].append(chapter_url)
                        else:
                            file_data["url"] = [chapter_url]

                        # Write the updated data back to the file
                        with open(incomplete_file_path, "w", encoding="utf-8") as file:
                            json.dump(file_data, file, ensure_ascii=True, indent=4)
                            
                        print(f"Saved incomplete chapters to {incomplete_file_path}, continuing to the next chapter.")
                        
                        i += 1
                        web = webdriver.Chrome()
                        web.get(self.url)
                        
                        search_box = web.find_element(By.XPATH, "//input[@placeholder='Nhập số chương...']")
                        search_box.send_keys(str(i+1))
                        search_box.send_keys(Keys.ENTER)
                        time.sleep(3)  # wait for the search results to load
                        
                        if len(web.window_handles) > 1:
                            # close the current tab
                            web.close()
                        link_rel = web.find_element(By.XPATH, "//h2[@class='current-chapter']").find_element(By.TAG_NAME, "a").get_attribute("href")
                        web.quit()
                        chapter_url = self.base_url + link_rel if not link_rel.startswith("http") else link_rel
                        print(f"Continuing with chapter {i+1} at URL: {chapter_url}")
                        pbar.update(1)
                        continue
                 
                tries += 1
                time.sleep(5)  # Wait before retrying
                # i -= 1
                continue  # Retry the current chapter
                    
            i += 1
            tries = 0
            
            # Uncomment the following lines if you want to save incomplete chapters in case of an error
                
                # print("An error occurred while processing chapters. Check error_log.txt for details.")
                # continue    # If an error occurs, save the current state and continue to the next chapter
        # Save the chapter links to the novel_info dictionary
        pbar.close()
        self.novel_info["chapter_links"] = chapter_links
        print(f"Total chapters found: {len(chapter_links)}")
        
        # return "chapter links"
        return chapter_links
    

    
    def get_chapter_(self, chapter_url):
        soup = BeautifulSoup(requests.get(chapter_url).text, 'html.parser')
        chapter_title = soup.find(class_="current-chapter").text.strip()
        chapter_content = str(soup.find("div", class_="truyen").prettify())
        
        
        # with open("chapter_content.json", "w", encoding="utf-8") as file:
        #     json.dump({"title": str(chapter_title), 
        #                "content": str(chapter_content.prettify())}, 
        #               file, ensure_ascii=False, indent=4)
        
        chapter = {
            "title": chapter_title,
            "content": chapter_content
        }
        
        # with open("chapter_content.json", "w", encoding="utf-8") as file:
        #     json.dump(chapter, file, ensure_ascii=False, indent=4)
            
        # print(f"Chapter '{chapter_title}' saved.")
        # return "chapter content"
        return chapter
            
            
    def get_all_chapters(self):
        # for i in tqdm(range(1, len(self.novel_info["chapter_links"] + 1))):
        for i in tqdm.tqdm(range(1, len(self.novel_info["chapter_links"]) + 1), desc="Processing chapters", unit="chapter"):
            chapter = self.get_chapter_(self.novel_info["chapter_links"][i-1])
            chapter["index"] = i
            # chapter_title = chapter["title"].replace(" ", "_").replace("/", "-")
            # with open(os.path.join(self.output_dir, f"{chapter_title}.html"), "w", encoding="utf-8") as file:
            #     file.write(chapter["content"])
            # print(f"Chapter '{chapter_title}' saved.")
            self.novel_info["chapters"].append(chapter)
            
        with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "w", encoding="utf-8") as file:
            json.dump(self.novel_info, file, ensure_ascii=True, indent=4)
            
        print(f"All chapters saved. Total chapters stored: {len(self.novel_info['chapters'])}")
            
        return "get all chapters"
            
    
    def get_chapter(self, chapter):
        if isinstance(chapter, str):
            if not chapter.startswith("http"):
                chapter = self.base_url + chapter
            chapter_index = self.novel_info["chapter_links"].index(chapter) + 1
        elif isinstance(chapter, str) and chapter.isdigit():
            chapter_index = int(chapter)
        elif isinstance(chapter, int):
            chapter_index = chapter
        
        for e in self.novel_info["chapters"]:
            if e["index"] == chapter_index:
                # return e
                print(f"Chapter {chapter_index} already exists in the novel_info, skipping...")
                return "get chapter exists"
        
        chapter_url = self.novel_info["chapter_links"][chapter_index - 1]
        chapter_data = self.get_chapter_(chapter_url)
        chapter_data["index"] = chapter_index
        self.novel_info["chapters"].append(chapter_data)
        with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "w", encoding="utf-8") as file:
            json.dump(self.novel_info, file, ensure_ascii=True, indent=4)
        print(f"Chapter {chapter_index} saved.")
        return "get chapter"
    
    
    def get_chapter_range(self, start, end):
        if isinstance(start, str):
            if not start.startswith("http"):
                start = self.base_url + start
            start_index = self.novel_info["chapter_links"].index(start) + 1
        elif isinstance(start, str) and start.isdigit():
            start_index = int(start)
        elif isinstance(start, int):
            start_index = start
        
        if isinstance(end, str):
            if not end.startswith("http"):
                end = self.base_url + end
            end_index = self.novel_info["chapter_links"].index(end) + 1
        elif isinstance(end, str) and end.isdigit():
            end_index = int(end)
        elif isinstance(end, int):
            end_index = end
        
        chapters = []
        # for i in range(start_index, end_index + 1):
        for i in tqdm.tqdm(range(start_index, end_index + 1), desc="Processing chapters", unit="chapter"):
            chapter = self.get_chapter_(self.novel_info["chapter_links"][i - 1])
            chapter["index"] = i
            chapters.append(chapter)
        
        self.novel_info["chapters"] = list(set(self.novel_info["chapters"] + chapters))
        
        with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "w", encoding="utf-8") as file:
            json.dump(self.novel_info, file, ensure_ascii=True, indent=4)
        print(f"Chapters {start_index} to {end_index} saved. Total chapters stored: {len(self.novel_info['chapters'])}")
        
        return "get chapter range"
                
        
    def make_chapter_book(self, chapter):
        for e in self.novel_info["chapters"]:
            if e["index"] == chapter:
                chapter_data = e
                break
        else:
            chapter_data = self.get_chapter(chapter)
        chap = epub.EpubBook()
        chap.set_identifier(f'{self.novel_info["title"].replace(" ", "_")}-chap_{chapter_data["index"]}')
        chap.set_language('vi')
        # chap.set_title(f"{self.novel_info['title']} - {chapter_data['title'] if re.match(r'^Chương \d+', chapter_data['title']) else ('Chương ' + str(chapter_data['index']) + ': ' + chapter_data['title'])}")
        if re.match(r'^Chương \d+', chapter_data['title']):
            chap.set_title(f"{self.novel_info['title']} - {chapter_data['title']}")
        else:
            chap.set_title(f"Chương {chapter_data['index']}: {chapter_data['title']}")
        
        chap.add_author(self.novel_info["author"])
        if "description" in self.novel_info:
            chap.add_metadata("DC", "description", self.novel_info["description"])
        # for genre in self.novel_info["genres"]:
        #     chap.add_metadata("DC", "subject", genre)
        # chap.set_cover("cover.jpg", open(f"{self.output_dir}/cover.jpg", "rb").read())
        # Add cover image

        style = epub.EpubItem(
            file_name="style.css",
            media_type="text/css",
            content=epub_style_css()
        )
        chap.add_item(style)
        chap.set_template("cover", epub_cover_xhtml())
        chap.set_template("chapter", epub_chapter_xhtml())
        
        # Add cover image
        toc = []
        spine = []

        if self.novel_info["cover_image"]:
            with open(f"{self.output_dir}/cover.jpg", "rb") as cover_file:
                cover_content = cover_file.read()
                chap.set_cover("cover.jpg", cover_content, create_page=True)
            
            spine.append("cover")

            cover_item = epub.EpubHtml(
                uid="cover",
                file_name="cover.jpg",
                media_type="image/jpeg",
                content=f"""
                <div id="cover">
                    <img src="cover.jpg" alt="Cover Image" />
                </div>
                """
            )

            cover_item.add_link(
                href="style.css",
                rel="stylesheet",
                type="text/css",
                )
            
        chap.add_item(cover_item)
        spine.append(cover_item)
        toc.append(epub.Link("cover.jpg", "Bìa sách", "cover"))
        
        # Add introduction  
        # <p>Thể loại: {', '.join(self.novel_info['genres']) if self.novel_info['genres'] else 'Khác'}</p>
        intro = epub.EpubHtml(
            title=f"Giới thiệu nội dung",
            file_name="intro.xhtml",
            lang='vi',
            content=f"""
            <h1>{self.novel_info['title']}</h1>
            <p>Tác giả: {self.novel_info['author']}</p>
            <p>Số chương: {self.novel_info['num_chapters']}</p>
            <p>Mô tả: {self.novel_info['description'] if 'description' in self.novel_info else '<p>Không có mô tả</p>'}</p>
            <p>Link truyện: <a href="{self.url}">{self.url}</a></p>
            
            <p>This project is inspired and copy style from lncrawler project by <a href="https://github.com/dipu-bd/lightnovel-crawler">dipu-bd/lightnovel-crawler</a>.</p>
            
            """
            # <p>Cover image: <img src="{self.novel_info['cover_image']}" alt="Cover Image" /></p>
        )

        intro.add_link(
            href="style.css",  
            rel="stylesheet",
            type="text/css",
        )

        chap.add_item(intro)
        toc.append(intro)
        spine.append(intro)
        spine.append("nav")
        
        c = epub.EpubHtml(
            title=chapter_data["title"],
            file_name=f"chap_{chapter_data['index']}.xhtml",
            lang='vi',
            content=f"{chapter_data['content']}"
        )
        c.add_link(
            href=style.file_name,
            rel="stylesheet",
            type="text/css",
        )
        chap.add_item(c)
        toc.append(c)
        spine.append(c)
        
        chap.toc = toc
        chap.spine = spine
        
        chap.add_item(epub.EpubNcx())
        chap.add_item(epub.EpubNav())
        
        epub.write_epub(f"{self.output_dir}/{self.novel_info['title']}_chap_{chapter_data['index']}.epub", chap, {})
        print(f"Chapter {chapter_data['index']} book created successfully.")
        return "make epub chapter"
    
    
    def make_all_book(self):
        book = epub.EpubBook()
        book.set_identifier(f'{self.novel_info["title"].replace(" ", "_")}-all_chapter')
        book.set_title(self.novel_info["title"])
        book.set_language('vi')
        # book.set_cover("cover.jpg", open(f"{self.output_dir}/cover.jpg", "rb").read())
        book.add_author(self.novel_info["author"])
        book.add_metadata("DC", "description", self.novel_info["description"] if "description" in self.novel_info else "<p>Không có mô tả</p>")
        # for genre in self.novel_info["genres"]:
        #     book.add_metadata("DC", "subject", genre)
        
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

        if self.novel_info["cover_image"]:
            with open(f"{self.output_dir}/cover.jpg", "rb") as cover_file:
                cover_content = cover_file.read()
                book.set_cover("cover.jpg", cover_content, create_page=True)
            
            spine.append("cover")

            cover_item = epub.EpubItem(
                uid="cover",
                file_name="cover.jpg",
                media_type="image/jpeg",
                content=f"""
                <div id="cover">
                    <img src="cover.jpg" alt="Cover Image" />
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
        toc.append(epub.Link("cover.jpg", "Bìa sách", "cover"))
        
        # Add introduction
        intro = epub.EpubHtml(
            title=f"Giới thiệu nội dung",
            file_name="intro.xhtml",
            # lang='vi',
            # <p>Thể loại: {', '.join(self.novel_info['genres']) if self.novel_info['genres'] else 'Khác'}</p>
            content=f"""
            <h1>{self.novel_info['title']}</h1>
            <p>Tác giả: {self.novel_info['author']}</p>
            
            <p>Số chương: {self.novel_info['num_chapters']}</p>
            <p>Mô tả: {self.novel_info['description'] if 'description' in self.novel_info else '<p>Không có mô tả</p>'}</p>
            <p>Link truyện: <a href="{self.url}">{self.url}</a></p>
            
            <p>This project is inspired and copy style from lncrawler project by 
            <a href="https://github.com/dipu-bd/lightnovel-crawler">dipu-bd/lightnovel-crawler</a>. 
            So if you like this project, please give some time to visit the original project.</p>
            </p>
            
            """
            # <p>Cover image: <img src="{self.novel_info['cover_image']}" alt="Cover Image" /></p>
        )
        intro.add_link(
        href="style.css",
        rel="stylesheet",
        type="text/css",
        )
        book.add_item(intro)
        toc.append(intro)
        spine.append(intro)
        spine.append("nav")
        
        # Add chapters
        # for chapter in self.novel_info["chapters"]:
        #     chapter_title = chapter["title"].replace(" ", "_").replace("/", "-")
        #     c = epub.EpubHtml(title=chapter_title, file_name=f"{chapter_title}.xhtml", lang='vi')
        #     c.content = f"<h1>{chapter['title']}</h1><p>{chapter['content']}</p>"
        #     book.add_item(c)
        #     book.toc.append(c)
        
        for i in tqdm.tqdm(range(1, len(self.novel_info["chapters"]) + 1), desc="Processing chapters", unit="chapter"):
            chapter = self.novel_info["chapters"][i - 1]
            c = epub.EpubHtml(
                title=chapter["title"],
                file_name=f"chap_{chapter['index']}.xhtml",
                # lang='vi',
                content=chapter['content'],
            )

            c.add_link(
                href=style.file_name,
                rel="stylesheet",
                type="text/css",
            )

            book.add_item(c)
            toc.append(c)
            spine.append(c)
        
        book.toc = toc
        book.spine = spine
        
        # Add navigation files
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        
        # Save the epub file
        epub.write_epub(f"{self.output_dir}/{self.novel_info['title']}.epub", book, {})
        
        print(f"Epub file '{self.novel_info['title']}.epub' created successfully.")
    
    
class WikiCrawler:
    def __init__(self, url, output_dir=""):
        self.url = url
        # "https://wattpad.com.vn/manh-su-tai-thuong-truyen-chu"
        self.base_url = self.get_base_url()
        # "https://wattpad.com.vn"
        self.title = self.get_title_from_link()
        # "manh su tai thuong"
        self.output_dir = f"outputs/{self.title}" if output_dir == "" else output_dir
        self.novel_info = {
            "title": None,
            "author": None,
            "cover_image": None,
            "num_chapters": None,
            "description": None,
            "genres": [],
            "novel_url": self.url,
            "start_chapter": None,
            "end_chapter": None,
            "chapter_links": [],
            "chapters": []}
        
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        
    
    def get_base_url(self):
        if self.url.startswith("http"):
            return self.url.split("/")[0] + "//" + self.url.split("/")[2]
        else:
            return "https://" + self.url.split("/")[0]
        
    def get_title_from_link(self):
        return self.url.split("/")[-1].replace("-", " ").replace("_", " ")


    def get_page_content_(self):
        web = webdriver.Chrome()
        web.get(self.url)
        # time.sleep(2)
        soup = BeautifulSoup(web.page_source, 'html.parser')
        
        web.quit()
        with open("new_page_content.html", "w", encoding="utf-8") as file:
            file.write(soup.prettify())
        print("Page content saved to new_page_content.html")
        return "page content"
        
        
    def get_novel_info_(self):
        # This method can be implemented to extract novel information
        print("getting novel info...")
        # web = webdriver.Chrome()
        # web.get(self.url)
        respose = requests.get(self.url)
        # soup = BeautifulSoup(web.page_source, 'html.parser')
        soup = BeautifulSoup(respose.text, 'html.parser')
        # time.sleep(2)
        
        # cover_image = web.find_element(By.XPATH, "//img[@itemprop='image']").get_attribute("src")
        # title = web.find_element(By.XPATH, "//span[@itemprop='name']").text
        # author = web.find_element(By.XPATH, "//a[@itemprop='author']").text
        book_info = soup.find("div", class_="book-info")
        cover_image_link = book_info.find("img", itemprop="image").get("src")
        ext =  cover_image_link.split(".")[-1]
        try:
            with requests.get(self.base_url+cover_image_link) as response:
                if response.status_code == 200:
                    with open(f"{self.output_dir}/cover.{ext}", "wb") as file:
                        file.write(response.content)
                    cover_image = f"{self.output_dir}/cover.{ext}"
                else:
                    print(f"Failed to retrieve cover image, status code: {response.status_code}")
                    cover_image = None
        except Exception as e:
            print(f"Error retrieving cover image: {e}")
            cover_image = None
        title = book_info.find(itemprop="name").text
        author = book_info.find(itemprop="author").text
        num_chapters = int(re.search(r'Số chương: (\d+)', soup.text).group(1))
        genres = [genre.text for genre in soup.find("li", class_="li--genres").find_all("a")]
        # genres = str(soup.find("li", class_="li--genres"))
        desc = str(soup.find("div", itemprop="description"))
        start_chapter = soup.find("a", string="Chương 1").get("href")
        end_chapter = soup.find("a", string="Chương cuối").get("href")
        # cover_image = soup.find("img", itemprop="image").get("src")
        # title = soup.find(class_="book-info").find(itemprop="name").text
        # author = soup.find("a", itemprop="author").text
        # num_chapters = int(re.search(r'Số chương: (\d+)', soup.text).group(1))
        # start_chapter = soup.find("a", string="Chương 1").get("href")
        # end_chapter = soup.find("a", string="Chương cuối").get("href")
        
        # web.quit()
        self.novel_info = {
            "title": title,
            "author": author,
            # "author": "Phong Dữ Thiên Mạc",
            "cover_image": cover_image,
            "num_chapters": num_chapters,
            "description": desc,
            "genres": genres,
            "novel_url": self.url,
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
            "chapter_links": [],
            "chapters": []
        }
        
        # compare novel info with existing info
        if os.path.exists(f"{self.output_dir}/{self.novel_info['title']}_info.json"):
            with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "r", encoding="utf-8") as file:
                existing_info = json.load(file)
            if existing_info["title"] == self.novel_info["title"] and \
               existing_info["author"] == self.novel_info["author"] and \
                existing_info["num_chapters"] == self.novel_info["num_chapters"] and \
                existing_info["start_chapter"] == self.novel_info["start_chapter"] and \
                existing_info["end_chapter"] == self.novel_info["end_chapter"]:
                print("Novel information is the same as the existing one, using existing data.")
                self.novel_info = existing_info
                # return True
            else:
                print("Novel information is different, updating the existing data.")
                if self.novel_info["num_chapters"] > existing_info["num_chapters"]:
                    print("Novel has new chapters, updating chapter links...")
                    old_chapter_index = existing_info["chapter_links"].index(existing_info["end_chapter"]) + 1
                    chapter_url = self.base_url + existing_info["end_chapter"] if existing_info["end_chapter"].startswith("/") else existing_info["end_chapter"]
                    self.novel_info["chapter_links"] = existing_info["chapter_links"][:old_chapter_index]
                    pbar = tqdm.tqdm(total=self.novel_info["num_chapters"] - old_chapter_index, desc="Processing chapters", unit="chapter")
                    for i in range(old_chapter_index, self.novel_info["num_chapters"] + 1):
                        # if (i+1)%10 == 0:
                        #     print(f"Processing chapter {i+1}/{self.novel_info['num_chapters']}")
                        # chapter_url = self.base_url+self.novel_info["end_chapter"] if i == 0 else chapter_url
                        soup = BeautifulSoup(requests.get(chapter_url).text, 'html.parser')
                        next_chapter = soup.find("a", class_="next").get("href")
                        self.novel_info["chapter_links"].append(chapter_url)
                        chapter_url = self.base_url+next_chapter
                        pbar.update(1)
                        time.sleep(0.8)  # To avoid overwhelming the server
                    pbar.close()
                        
                if self.novel_info["start_chapter"] != existing_info["start_chapter"]:
                    print("All chapters links have changed, updating all chapter links...")
                    self.novel_info["chapter_links"] = self.get_all_chapters_links_()
                    
        else:
            print("No existing novel information found, creating new save with all chapter links data. This may take a while...")
            self.novel_info["chapter_links"] = self.get_all_chapters_links_()
        
        with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "w", encoding="utf-8") as file:
            json.dump(self.novel_info, file, ensure_ascii=True, indent=4)
        print("Novel information saved to novel_info.json")
        print(f"Chapter links is retrieved: {not (len(self.novel_info['chapter_links']) == 0)}")
        return "novel info"
    
    
    def get_all_chapters_links_(self):
        print("getting all chapters links...")
        if not self.novel_info:
            print("Novel information not found. Please run get_novel_info() first.")
            return
        chapter_links = []
        chapter_url = self.base_url + self.novel_info["start_chapter"]
        i = 0
        tries = 0
        pbar = tqdm.tqdm(total=self.novel_info["num_chapters"], desc="Processing chapters", unit="chapter")
        while i < self.novel_info["num_chapters"]:
            try:
                # if (i+1)%10 == 0:
                #     print(f"Processing chapter {i+1}/{self.novel_info['num_chapters']}")
                chapter_url = self.base_url+self.novel_info["start_chapter"] if i == 0 else chapter_url
                soup = BeautifulSoup(requests.get(chapter_url).text, 'html.parser')
                next_chapter = soup.find(class_="next").get("href")
                chapter_links.append(chapter_url)
                chapter_url = self.base_url+next_chapter if next_chapter.startswith("/") else next_chapter
                pbar.update(1)
                time.sleep(0.1)  # To avoid overwhelming the server
            except Exception as e:
                print(f"\nError processing chapter {i+1}: {e}")
                print("Trying again...")
                
                if tries > 5:    
                    print("Too many errors, trying again one more time with selenium...")                
                    try:
                        web = webdriver.Chrome()
                        web.get(self.url)
                        search_box = web.find_element(By.XPATH, "//input[@placeholder='Nhập số chương...']")
                        search_box.send_keys(str(i+1))
                        search_box.send_keys(Keys.ENTER)
                        time.sleep(3)
                        if len(web.window_handles) > 1:
                            web.switch_to.window(web.window_handles[0])
                        link_rel = web.find_element(By.XPATH, "//h2[@class='current-chapter']").find_element(By.TAG_NAME, "a").get_attribute("href")
                        chapter_url = self.base_url + link_rel if not link_rel.startswith("http") else link_rel
                        web.quit()
                        print(f"Chapter {i+1} found at URL: {chapter_url}")
                        print(f"Continuing with chapter {i+1} at URL: {chapter_url}")
                        continue
                    except Exception as e:
                        print("Too many errors, saving current state and continuing to the next chapter.")
                        tries = 0
                        # Save the current state in case of an error
                        self.novel_info["chapter_links"] = chapter_links
                        with open(f"{self.output_dir}/error_log.txt", "a", encoding="utf-8") as error_file:
                            error_file.write(f"Error processing chapter {i+1}: {e}\nChapter URL: {chapter_url}\n\n")
                        # Create the file if it doesn't exist, or load existing data
                        incomplete_file_path = f"{self.output_dir}/incomplete.json"

                        if os.path.exists(incomplete_file_path):
                            with open(incomplete_file_path, "r", encoding="utf-8") as file:
                                file_data = json.load(file)
                        else:
                            file_data = {}

                        # Update the data
                        if "chapter" in file_data:
                            file_data["chapter"].append(i+1)
                        else:
                            file_data["chapter"] = [i+1]

                        if "url" in file_data:
                            file_data["url"].append(chapter_url)
                        else:
                            file_data["url"] = [chapter_url]

                        # Write the updated data back to the file
                        with open(incomplete_file_path, "w", encoding="utf-8") as file:
                            json.dump(file_data, file, ensure_ascii=True, indent=4)
                            
                        print(f"Saved incomplete chapters to {incomplete_file_path}, continuing to the next chapter.")
                        
                        i += 1
                        web = webdriver.Chrome()
                        web.get(self.url)
                        
                        search_box = web.find_element(By.XPATH, "//input[@placeholder='Nhập số chương...']")
                        search_box.send_keys(str(i+1))
                        search_box.send_keys(Keys.ENTER)
                        time.sleep(3)  # wait for the search results to load
                        
                        if len(web.window_handles) > 1:
                            # close the current tab
                            web.close()
                        link_rel = web.find_element(By.XPATH, "//h2[@class='current-chapter']").find_element(By.TAG_NAME, "a").get_attribute("href")
                        web.quit()
                        chapter_url = self.base_url + link_rel if not link_rel.startswith("http") else link_rel
                        print(f"Continuing with chapter {i+1} at URL: {chapter_url}")
                        pbar.update(1)
                        continue
                 
                tries += 1
                time.sleep(5)  # Wait before retrying
                # i -= 1
                continue  # Retry the current chapter
                    
            i += 1
            tries = 0
            
            # Uncomment the following lines if you want to save incomplete chapters in case of an error
                
                # print("An error occurred while processing chapters. Check error_log.txt for details.")
                # continue    # If an error occurs, save the current state and continue to the next chapter
        # Save the chapter links to the novel_info dictionary
        pbar.close()
        self.novel_info["chapter_links"] = chapter_links
        print(f"Total chapters found: {len(chapter_links)}")
        
        # return "chapter links"
        return chapter_links
    

    
    def get_chapter_(self, chapter_url):
        soup = BeautifulSoup(requests.get(chapter_url).text, 'html.parser')
        chapter_title = soup.find(class_="current-chapter").text.strip()
        chapter_content = str(soup.find("div", class_="truyen").prettify())
        
        
        # with open("chapter_content.json", "w", encoding="utf-8") as file:
        #     json.dump({"title": str(chapter_title), 
        #                "content": str(chapter_content.prettify())}, 
        #               file, ensure_ascii=False, indent=4)
        
        chapter = {
            "title": chapter_title,
            "content": chapter_content
        }
        
        # with open("chapter_content.json", "w", encoding="utf-8") as file:
        #     json.dump(chapter, file, ensure_ascii=False, indent=4)
            
        # print(f"Chapter '{chapter_title}' saved.")
        # return "chapter content"
        return chapter
            
            
    def get_all_chapters(self):
        # for i in tqdm(range(1, len(self.novel_info["chapter_links"] + 1))):
        # for i in tqdm.tqdm(range(1, len(self.novel_info["chapter_links"]) + 1), desc="Processing chapters", unit="chapter"):
        pbar = tqdm.tqdm(range(1, len(self.novel_info["chapter_links"]) + 1), desc="Processing chapters", unit="chapter")
        i = 1
        tries = 0
        available_chapters = [i.get("index") for i in self.novel_info["chapters"]]
        while i <= len(self.novel_info["chapter_links"]):
            try:
                if i in available_chapters:
                    print(f"Chapter {i} already exists in the novel_info, skipping...")
                    pbar.update(1)
                    i += 1
                    continue
                else:
                    chapter = self.get_chapter_(self.novel_info["chapter_links"][i-1])
                chapter["index"] = i
                # chapter_title = chapter["title"].replace(" ", "_").replace("/", "-")
                # with open(os.path.join(self.output_dir, f"{chapter_title}.html"), "w", encoding="utf-8") as file:
                #     file.write(chapter["content"])
                # print(f"Chapter '{chapter_title}' saved.")
                self.novel_info["chapters"].append(chapter)
                pbar.update(1)
                i += 1
                tries = 0
                # time.sleep(0.2)  # To avoid overwhelming the server
            except Exception as e:
                print(f"\nError processing chapter {i}: {e}")
                print("Trying again...")

                if tries > 5:
                    print(f"Too many errors, saving current state and continuing to the next chapter.")
                    tries = 0
                    # Save the current state in case of an error
                    with open(f"{self.output_dir}/error_chap.txt", "a", encoding="utf-8") as error_file:
                        error_file.write(f"Error processing chapter {i}: {e}\n")
                        error_file.write(f"Chapter URL: {self.novel_info['chapter_links'][i-1]}\n")
                    i += 1
                    # Create the file if it doesn't exist, or load existing data
                    incomplete_file_path = f"{self.output_dir}/incomplete.json"
                    if os.path.exists(incomplete_file_path):
                        with open(incomplete_file_path, "r", encoding="utf-8") as file:
                            file_data = json.load(file)
                    else:
                        file_data = {"chapters": [], "url": self.url}
                    # Update the data
                    if "chapters" in file_data:
                        file_data["chapters"].append({
                            "index": i,
                            "url": self.novel_info["chapter_links"][i-1],
                            "title": chapter["title"] if "title" in chapter else f"Chương {i}"
                        })
                    else:
                        file_data["chapters"] = [{
                            "index": i,
                            "url": self.novel_info["chapter_links"][i-1],
                            "title": chapter["title"] if "title" in chapter else f"Chương {i}"
                        }]
                    
                tries += 1
                time.sleep(1)  # Wait before retrying
                        
        pbar.close()
        # Save the novel info with all chapters 
        with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "w", encoding="utf-8") as file:
            json.dump(self.novel_info, file, ensure_ascii=True, indent=4)
            
        print(f"All chapters saved. Total chapters stored: {len(self.novel_info['chapters'])}")
            
        return "get all chapters"
            
    
    def get_chapter(self, chapter):
        if isinstance(chapter, str):
            if not chapter.startswith("http"):
                chapter = self.base_url + chapter
            chapter_index = self.novel_info["chapter_links"].index(chapter) + 1
        elif isinstance(chapter, str) and chapter.isdigit():
            chapter_index = int(chapter)
        elif isinstance(chapter, int):
            chapter_index = chapter
        
        for e in self.novel_info["chapters"]:
            if e["index"] == chapter_index:
                # return e
                print(f"Chapter {chapter_index} already exists in the novel_info, skipping...")
                return "get chapter exists"
        
        chapter_url = self.novel_info["chapter_links"][chapter_index - 1]
        chapter_data = self.get_chapter_(chapter_url)
        chapter_data["index"] = chapter_index
        self.novel_info["chapters"].append(chapter_data)
        with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "w", encoding="utf-8") as file:
            json.dump(self.novel_info, file, ensure_ascii=True, indent=4)
        print(f"Chapter {chapter_index} saved.")
        return "get chapter"
    
    
    def get_chapter_range(self, start, end):
        if isinstance(start, str):
            if not start.startswith("http"):
                start = self.base_url + start
            start_index = self.novel_info["chapter_links"].index(start) + 1
        elif isinstance(start, str) and start.isdigit():
            start_index = int(start)
        elif isinstance(start, int):
            start_index = start
        
        if isinstance(end, str):
            if not end.startswith("http"):
                end = self.base_url + end
            end_index = self.novel_info["chapter_links"].index(end) + 1
        elif isinstance(end, str) and end.isdigit():
            end_index = int(end)
        elif isinstance(end, int):
            end_index = end
        
        available_chapters = [i.get("index") for i in self.novel_info["chapters"]]
        chapters = []
        # for i in range(start_index, end_index + 1):
        for i in tqdm.tqdm(range(start_index, end_index + 1), desc="Processing chapters", unit="chapter"):
            if i in available_chapters:
                print(f"Chapter {i} already exists in the novel_info, skipping...")
                continue
            else:
                chapter = self.get_chapter_(self.novel_info["chapter_links"][i - 1])
                chapter["index"] = i
                # chapters.append(chapter)
                self.novel_info["chapters"].append(chapter)
        
        # self.novel_info["chapters"] = list(set(self.novel_info["chapters"] + chapters))
        
        with open(f"{self.output_dir}/{self.novel_info['title']}_info.json", "w", encoding="utf-8") as file:
            json.dump(self.novel_info, file, ensure_ascii=True, indent=4)
        print(f"Chapters {start_index} to {end_index} saved. Total chapters stored: {len(self.novel_info['chapters'])}")
        
        return "get chapter range"
                
        
    # def make_chapter_book(self, chapter):
    #     if isinstance(chapter, str):
    #         if not chapter.startswith("http"):
    #             chapter = self.base_url + chapter
    #         chapter_index = self.novel_info["chapter_links"].index(chapter) + 1
    #     elif isinstance(chapter, str) and chapter.isdigit():
    #         chapter_index = int(chapter)
    #     elif isinstance(chapter, int):
    #         chapter_index = chapter
    #     for e in self.novel_info["chapters"]:
    #         if e["index"] == chapter_index:
    #             chapter_data = e
    #             break
    #     else:
    #         chapter_data = self.get_chapter(chapter)
    #     chap = epub.EpubBook()
    #     chap.set_identifier(f'{self.novel_info["title"].replace(" ", "_")}-chap_{chapter_data["index"]}')
    #     chap.set_language('vi')
    #     # chap.set_title(f"{self.novel_info['title']} - {chapter_data['title'] if re.match(r'^Chương \d+', chapter_data['title']) else ('Chương ' + str(chapter_data['index']) + ': ' + chapter_data['title'])}")
    #     if re.match(r'^Chương \d+', chapter_data['title']):
    #         chap.set_title(f"{self.novel_info['title']} - {chapter_data['title']}")
    #     else:
    #         chap.set_title(f"Chương {chapter_data['index']}: {chapter_data['title']}")
        
    #     chap.add_author(self.novel_info["author"])
    #     if "description" in self.novel_info:
    #         chap.add_metadata("DC", "description", self.novel_info["description"])
    #     # for genre in self.novel_info["genres"]:
    #     #     chap.add_metadata("DC", "subject", genre)
    #     # chap.set_cover("cover.jpg", open(f"{self.output_dir}/cover.jpg", "rb").read())
    #     # Add cover image

    #     style = epub.EpubItem(
    #         file_name="style.css",
    #         media_type="text/css",
    #         content=epub_style_css()
    #     )
    #     chap.add_item(style)
    #     chap.set_template("cover", epub_cover_xhtml())
    #     chap.set_template("chapter", epub_chapter_xhtml())
        
    #     # Add cover image
    #     toc = []
    #     spine = []

    #     if self.novel_info["cover_image"]:
    #         with open(f"{self.output_dir}/cover.jpg", "rb") as cover_file:
    #             cover_content = cover_file.read()
    #             chap.set_cover("cover.jpg", cover_content, create_page=True)
            
    #         spine.append("cover")

    #         cover_item = epub.EpubHtml(
    #             uid="cover",
    #             file_name="cover.jpg",
    #             media_type="image/jpeg",
    #             content=f"""
    #             <div id="cover">
    #                 <img src="cover.jpg" alt="Cover Image" />
    #             </div>
    #             """
    #         )

    #         cover_item.add_link(
    #             href="style.css",
    #             rel="stylesheet",
    #             type="text/css",
    #             )
            
    #     chap.add_item(cover_item)
    #     spine.append(cover_item)
    #     toc.append(epub.Link("cover.jpg", "Bìa sách", "cover"))
        
    #     # Add introduction  
    #     # <p>Thể loại: {', '.join(self.novel_info['genres']) if self.novel_info['genres'] else 'Khác'}</p>
    #     intro = epub.EpubHtml(
    #         title=f"Giới thiệu nội dung",
    #         file_name="intro.xhtml",
    #         lang='vi',
    #         content=f"""
    #         <h1>{self.novel_info['title']}</h1>
    #         <p>Tác giả: {self.novel_info['author']}</p>
    #         <p>Số chương: {self.novel_info['num_chapters']}</p>
    #         <p>Mô tả: {self.novel_info['description'] if 'description' in self.novel_info else '<p>Không có mô tả</p>'}</p>
    #         <p>Link truyện: <a href="{self.novel_info['novel_url']}">{self.novel_info['novel_url']}</a></p>
            
    #         <p>This project is inspired and copy style from lncrawler project by <a href="https://github.com/dipu-bd/lightnovel-crawler">dipu-bd/lightnovel-crawler</a>.</p>
    #         <p><i>Generated by WikiCrawler of Nguyen Hai Dang</i></p>
    #         """
    #         # <p>Cover image: <img src="{self.novel_info['cover_image']}" alt="Cover Image" /></p>
    #     )

    #     intro.add_link(
    #         href="style.css",  
    #         rel="stylesheet",
    #         type="text/css",
    #     )

    #     chap.add_item(intro)
    #     toc.append(intro)
    #     spine.append(intro)
    #     spine.append("nav")
        
    #     c = epub.EpubHtml(
    #         title=chapter_data["title"],
    #         file_name=f"chap_{chapter_data['index']}.xhtml",
    #         lang='vi',
    #         content=f"{chapter_data['content']}"
    #     )
    #     c.add_link(
    #         href=style.file_name,
    #         rel="stylesheet",
    #         type="text/css",
    #     )
    #     chap.add_item(c)
    #     toc.append(c)
    #     spine.append(c)
        
    #     chap.toc = toc
    #     chap.spine = spine
        
    #     chap.add_item(epub.EpubNcx())
    #     chap.add_item(epub.EpubNav())
        
    #     epub.write_epub(f"{self.output_dir}/{self.novel_info['title']}_chap_{chapter_data['index']}.epub", chap, {})
    #     print(f"Chapter {chapter_data['index']} book created successfully.")
    #     return "make epub chapter"
    
    
    # def make_all_book(self):
    #     book = epub.EpubBook()
    #     book.set_identifier(f'{self.novel_info["title"].replace(" ", "_")}-all_chapter')
    #     book.set_title(self.novel_info["title"])
    #     book.set_language('vi')
    #     # book.set_cover("cover.jpg", open(f"{self.output_dir}/cover.jpg", "rb").read())
    #     book.add_author(self.novel_info["author"])
    #     book.add_metadata("DC", "description", self.novel_info["description"] if "description" in self.novel_info else "<p>Không có mô tả</p>")
    #     for genre in self.novel_info["genres"]:
    #         book.add_metadata("DC", "subject", genre)
        
    #     style = epub.EpubItem(
    #         file_name="style.css",
    #         media_type="text/css",
    #         content=epub_style_css()
    #     )
        
    #     book.add_item(style)
        
    #     book.set_template("cover", epub_cover_xhtml())
    #     book.set_template("chapter", epub_chapter_xhtml())

    #     toc = []
    #     spine = []

    #     if self.novel_info["cover_image"]:
    #         with open(f"{self.output_dir}/cover.jpg", "rb") as cover_file:
    #             cover_content = cover_file.read()
    #             book.set_cover("cover.jpg", cover_content, create_page=True)
            
    #         spine.append("cover")

    #         cover_item = epub.EpubHtml(
    #             uid="cover",
    #             file_name="cover.jpg",
    #             media_type="image/jpeg",
    #             content=f"""
    #             <div id="cover">
    #                 <img src="cover.jpg" alt="Cover Image" />
    #             </div>
    #             """
    #         )

    #         cover_item.add_link(
    #             href="style.css",
    #             rel="stylesheet",
    #             type="text/css",
    #             )
            
    #     book.add_item(cover_item)
    #     spine.append(cover_item)
    #     toc.append(epub.Link("cover.jpg", "Bìa sách", "cover"))
        
    #     # Add introduction
    #     intro = epub.EpubHtml(
    #         title=f"Giới thiệu nội dung",
    #         file_name="intro.xhtml",
    #         # lang='vi',
    #         # <p>Thể loại: {', '.join(self.novel_info['genres']) if self.novel_info['genres'] else 'Khác'}</p>
    #         content=f"""
    #         <h1>{self.novel_info['title']}</h1>
    #         <p>Tác giả: {self.novel_info['author']}</p>
            
    #         <p>Số chương: {self.novel_info['num_chapters']}</p>
    #         <p>Mô tả: {self.novel_info['description'] if 'description' in self.novel_info else '<p>Không có mô tả</p>'}</p>
    #         <p>Link truyện: <a href="{self.url}">{self.url}</a></p>
            
    #         <p>This project is inspired and copy style from <b>lncrawler</b> project by <a href="https://github.com/dipu-bd/lightnovel-crawler">dipu-bd/lightnovel-crawler</a>.</p>
    #         <p>If you like this project, please give some time to visit the original project.</p>
    #         <p><i>Generated by WikiCrawler of Nguyen Hai Dang</i></p>
            
    #         """
    #         # <p>Cover image: <img src="{self.novel_info['cover_image']}" alt="Cover Image" /></p>
    #     )
    #     intro.add_link(
    #     href="style.css",
    #     rel="stylesheet",
    #     type="text/css",
    #     )
    #     book.add_item(intro)
    #     toc.append(intro)
    #     spine.append(intro)
        
    #     if self.novel_info["num_chapters"] <= 100:
    #         spine.append("nav")
        
    #     # Add chapters
    #     # for chapter in self.novel_info["chapters"]:
    #     #     chapter_title = chapter["title"].replace(" ", "_").replace("/", "-")
    #     #     c = epub.EpubHtml(title=chapter_title, file_name=f"{chapter_title}.xhtml", lang='vi')
    #     #     c.content = f"<h1>{chapter['title']}</h1><p>{chapter['content']}</p>"
    #     #     book.add_item(c)
    #     #     book.toc.append(c)
        
    #     for i in tqdm.tqdm(range(1, len(self.novel_info["chapters"]) + 1), desc="Processing chapters", unit="chapter"):
    #         chapter = self.novel_info["chapters"][i - 1]
    #         c = epub.EpubHtml(
    #             title=chapter["title"],
    #             file_name=f"chap_{chapter['index']}.xhtml",
    #             # lang='vi',
    #             content=f"""
    #             <div class="chapter" id="chapter-{chapter['index']}">
    #                 <h1>{chapter['title'] if 'Chương' in chapter['title'] else 'Chương ' + str(chapter['index']) + ': ' + chapter['title']}</h1>
    #             </div>
    #             {chapter['content']}
    #             """,
    #         )

    #         c.add_link(
    #             href=style.file_name,
    #             rel="stylesheet",
    #             type="text/css",
    #         )

    #         book.add_item(c)
    #         toc.append(c)
    #         spine.append(c)
        
    #     book.toc = toc
    #     book.spine = spine
        
    #     # Add navigation files
    #     book.add_item(epub.EpubNcx())
    #     book.add_item(epub.EpubNav())
        
    #     # Save the epub file
    #     epub.write_epub(f"{self.output_dir}/{self.novel_info['title']}.epub", book, {})
        
    #     print(f"Epub file '{self.novel_info['title']}.epub' created successfully.")


    def show_novel_info(self):
        if not self.novel_info:
            print("Novel information not found. Please run get_novel_info() first.")
            return
        print(f"Title: {self.novel_info['title']}")
        print(f"Author: {self.novel_info['author']}")
        print(f"Cover Image: {self.novel_info['cover_image']}")
        print(f"Number of Chapters: {self.novel_info['num_chapters']}")
        print(f"Description: {self.novel_info['description'] if 'description' in self.novel_info else 'No description available'}")
        print(f"Genres: {', '.join(self.novel_info['genres']) if self.novel_info['genres'] else 'No genres available'}")
        print(f"Novel URL: {self.novel_info['novel_url']}")
        print(f"Start Chapter: {self.novel_info['start_chapter']}")
        print(f"End Chapter: {self.novel_info['end_chapter']}")
        print(f"Chapter Links: {len(self.novel_info['chapter_links'])} links found")
        print("Chapters currently stored:")
        for chapter in self.novel_info["chapters"]:
            print(f"  - Chapter {chapter['index']}: {chapter['title']}")

        return "show novel info"
    
    def __book_info_json__(self):
        if not self.novel_info:
            print("Novel information not found. Please run get_novel_info() first.")
            return
        if not os.path.exists(self.output_dir):
            print("The output directory does not exist, creating it...")
            os.makedirs(self.output_dir)
            print("The novel information is currently emtpy, note that before you export the ebook, or run any function that requires the novel information, you have to run get_novel_info() first.")

        return self.output_dir

def test_novel():
    test_link = "https://wattpad.com.vn/manh-su-tai-thuong-truyen-chu"
    test_chapter = "https://wattpad.com.vn/manh-su-tai-thuong-truyen-chu/chuong-7-xxLpo8KKf6jK"

    crawler = WikiCrawler(test_link, "test_output/manh-su-tai-thuong")
    print(crawler.get_novel_info_())
    print(crawler.get_chapter_(test_chapter))

            
# def __main__():
#     # test_link = "https://wattpad.com.vn/manh-su-tai-thuong-truyen-chu"
#     # test_chapter = "https://wattpad.com.vn/manh-su-tai-thuong-truyen-chu/chuong-1-pC9X6teJvsJd"
#     # crawler = test()
#     # # print(crawler.get_page_content())
#     # print(crawler.get_novel_info_())
#     # # print(crawler.get_all_chapters_links())
#     # print(crawler.get_chapter(test_chapter))
#     # print(crawler.make_chapter_book(1))
#     # print(crawler.get_chapter(2))
#     # # print(crawler.get_chapter_range(1, 5))


#     link = input("Enter the Wattpad/Wikidich novel link: ")
#     output_dir = input("Enter the output directory (default is the novel title of the current directory): ")
#     crawler = WikiCrawler(link, output_dir)
#     crawler.get_novel_info_()

#     chapter_option = input("""Please select a chapter option:
# - type get_all to get all chapters of the novel
# - type get_chapter to get a specific chapter
# - type get_chapter_range to get a range of chapters
# - type exit to exit the program
# Enter your choice: """)
    
#     if chapter_option == "get_all":
#         # print(crawler.get_all_chapters())
#         crawler.get_all_chapters()
#     elif chapter_option == "get_chapter":
#         chapter = input("Enter the chapter number or URL: ")
#         crawler.get_chapter(chapter)
#     elif chapter_option == "get_chapter_range":
#         start = input("Enter the start chapter number or URL: ")
#         end = input("Enter the end chapter number or URL: ")
#         crawler.get_chapter_range(start, end)
#     elif chapter_option == "exit":
#         print("Exiting the program.")
#         return
    
#     show_info_option = input(f"Do you want to show the novel information? (y/n): ")
#     if show_info_option.lower() == 'y':
#         crawler.show_novel_info()
#     else:
#         print(f"You can always look for full info in the file {crawler.output_dir}/novel_info.json")

#     make_book_option = input("""How do you want to save the chapters?
# 1. Save as individual chapter books
# 2. Save as a single book with chapters stored in the novel_info.json file of output directory (you have to have the chapters content in the novel_info.json file)
# 3. Exit (Note that the chapters you crawled will still be saved in the novel_info.json file for later use)
# Enter your choice (1/2/3): """)
#     if make_book_option == "1":
#         if chapter_option == "get_all":
#             for i in range(1, crawler.novel_info["num_chapters"] + 1):
#                 # crawler.make_chapter_book(i)
#                 create_epub(crawler.novel_info, crawler.output_dir, chapter_num=[i])
#         elif chapter_option == "get_chapter":
#             # crawler.make_chapter_book(chapter)
#             create_epub(crawler.novel_info, crawler.output_dir, chapter_num=[chapter])
#         elif chapter_option == "get_chapter_range":
#             for i in range(int(start), int(end) + 1):
#                 # crawler.make_chapter_book(i)
#                 create_epub(crawler.novel_info, crawler.output_dir, chapter_num=[i])
#     elif make_book_option == "2":
#         # crawler.make_all_book()
#         if chapter_option == "get_all":
#             create_epub(crawler.novel_info, crawler.output_dir)
#         elif chapter_option == "get_chapter":
#             create_epub(crawler.novel_info, crawler.output_dir, chapter_num=[chapter])
#         elif chapter_option == "get_chapter_range":
#             create_epub(crawler.novel_info, crawler.output_dir, chapter_num=(int(start), int(end)))
#     elif make_book_option == "3":
#         print("Exiting the program.")
#         return
#     else:
#         print("Invalid option. Exiting the program.")
#         return

#     print("Operation completed successfully.")
    
 
    
    
# if __name__ == "__main__":
#     __main__()
#     # test_novel()