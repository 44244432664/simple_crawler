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
# driver = webdriver.Chrome()

class QQCrawler:
    def __init__(self, comic_link="https://truyenqqgo.com/truyen-tranh/ba-xa-nha-toi-den-tu-ngan-nam-truoc-16030"):
        self.comic_link = comic_link
        # self.chapter_links = []
        # self.img_links = []
        self.referrer = self.get_referrer()
        # self.referrer = "https://truyenqqgo.com/"
        self.comic_name = self.get_comic_name()
        self.host = self.referrer
        self.path = os.path.join(os.getcwd(), "outputs", self.comic_name)
        if not os.path.exists(self.path):
            print("Creating comic directory...")
            os.makedirs(self.path, exist_ok=True)
            print("Comic directory created: ", self.path)
        else:
            print("Comic directory already exists: ", self.path)
        print("Comic directory path: ", self.path)
    
    def get_comic_name(self):
        """
        Get comic name from comic link
        """
        code = re.findall(r'\d+', self.comic_link)[-1]
        # name = re.search(r'truyen-tranh/(.+?)-\d+', self.comic_link)
        # name = name.group(1) if name else "Unknown Comic"
        name = re.search(r'truyen-tranh/(.+?)-' + code, self.comic_link)
        name = name.group(1) if name else "Unknown Comic"
        return name
    
    def get_referrer(self):
        """
        Get referrer from comic link
        """
        domain = re.search(r'https?://([^/]+)/', self.comic_link)
        if domain:
            self.referrer = f"https://{domain.group(1)}/"
        else:
            print("Invalid comic link")
            return "error"
        print("Referrer set to: ", self.referrer)
        return self.referrer
        # return "referrer set"
        
    def get_all_chapter_link(self):
        chapter_links = []
        response = requests.get(self.comic_link)
        if response.status_code != 200:
            print("Cannot get all chapter link")
            return "error"
        response = BeautifulSoup(response.text, "html.parser")
        # response = response.find_all(
        #     "div", class_="widget-content")  # get all link
        response = response.find_all("a", target="_self")  # get all link
        # response = response[1].find_all("a")
        # for i in response:
        #     if i.get("href").endswith(".html"):
        #         links.append(i.get("href"))
        # return links
        
        # return response to txt file
        # open("response.txt", "w", encoding="utf-8").write(response.prettify())
        for r in response:
            chapter_links.append(r.get("href"))
        # open("chapter_links.txt", "w", encoding="utf-8").write("\n".join(self.chapter_links))
        print("Total links: ", len(chapter_links))
        # print(self.chapter_links[0:5])
        # return "chapter links"
        chapter_links.reverse()  # reverse the order of chapter links
        time.sleep(0.2)  # sleep for 0.2 seconds to let reverse work properly
        with open(f"{self.path}/chapter_links.json", "w", encoding="utf-8") as f:
            import json
            json.dump(chapter_links, f, ensure_ascii=False, indent=4)
        return chapter_links


    def get_chapter_data(self, chapter):
        img_links = []
        if isinstance(chapter, int):
            chapter_link = self.comic_link+"-chap-"+str(chapter)+".html"
            chapter_num = chapter
        elif isinstance(chapter, float):
            dec =  str((chapter * 10) % 10)
            int_chapter = str(math.floor(chapter))
            chapter_link = self.comic_link+"-chap-"+int_chapter+"-"+dec+".html"
            chapter_num = int_chapter+"."+dec
        elif isinstance(chapter, str):
            if "https://" in chapter:
                chapter_link = chapter
            else:
                chapter_link = self.host + chapter
            # if not re.search(r'\d+', chapter).group(2):
            #     chapter_num = re.search(r'\d+', chapter).group(1)
            # else:
            #     chapter_num = re.search(r'\d+', chapter).group(1) + "." + re.search(r'\d+', chapter).group(2)

            # if re.search(r'\d+', chapter).group(2):
            #     chapter_num = re.search(r'\d+', chapter).group(1) + "." + re.search(r'\d+', chapter).group(2)
            # else:
            #     chapter_num = re.search(r'\d+', chapter).group(1)
            # print("chapter = ", chapter)
            # print("chapter grp 1 =  ", re.findall(r'\d+', chapter))
            chap = chapter.split("chap-")
            if len(re.findall(r'\d+', chap[-1])) < 2:
                chapter_num = re.findall(r'\d+', chap[-1])[0]
            else:
                chapter_num = re.findall(r'\d+', chap[-1])[0] + "." + re.findall(r'\d+', chap[-1])[1]

        else:
            print("Chapter must be int or str link")
            return "error"
        
        if not os.path.exists(f"{self.path}/chapter {chapter_num}"):
            # print(f"Creating directory for chapter {chapter_num}...")
            # create directory for chapter
            chapter_path = os.path.join(self.path, f"chapter {chapter_num}")
            os.makedirs(chapter_path, exist_ok=True)
            # print(f"Directory created: {chapter_path}")
        
        response = requests.get(chapter_link)
        if response.status_code != 200:
            print("Cannot get chapter data")
            return "error"
        response = BeautifulSoup(response.text, "html.parser")
        
        # open("chapter_html.txt", "w", encoding="utf-8").write(response.prettify())
        
        # return all links in the page
        imgs = response.find_all("img", class_="lazy")
        
        for i in imgs:
            # links.append(str(i))
            img_links.append(i.get("src"))
        # remove ad img (img[0])
        # self.img_links = self.img_links[1:]
        # open("chapter_data.txt", "w", encoding="utf-8").write("\n".join(self.img_links))
            
        # print("Total img links: ", len(img_links))
        # print("Total links: ", len(response))
        # print(links[0:5])
        # return "chapter img"
        return chapter_num, img_links


    def get_chapter_images(self, img_links, chapter_num): # links_of_img, referer="https://truyenqqgo.com/"
        fnames = []
        img_extensions = [".jpg", ".jpeg", ".png", ".gif"]
        header = {
            'referer'   : self.referrer,
            'sec-ch-ua' : '"Chromium";v="137", "Google Chrome";v="137", "Not.A/Brand";v="24"',
            'sec-ch-ua-mobile' : '?0',
            'sec-ch-ua-platform' : '"macOS"',
            'user-agent' : 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137c.0.0.0 Safari/537.36'
        }
        i = 0
        tries = 0
        # for i in range(len(img_links)):
        while i < len(img_links):
        # for i in range(6):  # limit to first 6 images for testing
            try:
                with requests.get(img_links[i], timeout=10, headers=header) as r:
                    if r.status_code == 200:
                        # detect image extension
                        ext = os.path.splitext(img_links[i])[1].lower()
                        # remove extensive query string if exists
                        if "?" in ext:
                            ext = ext.split("?")[0]
                        if ext not in img_extensions:
                            print("Image extension not supported: ", ext)
                            continue
                        # save image
                        if not os.path.exists(f"{self.path}/chapter {chapter_num}"):
                            os.makedirs(f"{self.path}/chapter {chapter_num}")
                        # save image with index
                        if os.path.exists(f"{self.path}/chapter {chapter_num}"+str(i)+ext):
                            print("File already exists: ", str(i)+ext)
                            continue
                        with open(f"{self.path}/chapter {chapter_num}/"+str(i)+ext, "wb") as f:
                        # with open("images/"+str(i)+".jpg", "wb") as f:
                            f.write(r.content)
                            fnames.append(str(i)+".jpg")
                        # print("Saved "+str(i)+".jpg")
                        i += 1
                        tries = 0
                        time.sleep(0.1)  # sleep for 0.1 seconds to avoid being blocked
                    else:
                        print("Error: ", r.status_code)
                        print("Trying again...")
                        tries += 1
                        if tries >= 5:
                            print("Too many tries, skipping image...")
                            with open(f"{self.path}/chapter {chapter_num}/error_log.txt", "a") as f:
                                f.write(f"Error: {r.status_code} for image {img_links[i]}\n")
                            i += 1
                        time.sleep(1)  # sleep for 1 second before trying again
                        continue
            except Exception as e:
                print("requests error: ", e)
                print("Trying again...")
                tries += 1
                if tries >= 5:
                    print("Too many tries, skipping image...")
                    with open(f"{self.path}/chapter {chapter_num}/error_log.txt", "a") as f:
                        f.write(f"Error: {e} for image {img_links[i]}\n")
                    i += 1
                time.sleep(1)  # sleep for 1 second before trying again
                continue
        # print("Total images saved: ", len(fnames))
        # # return fnames  # return list of filenames
        return "imgs"

    
    # def create_pdf(self, chapter, delete_page_0=False):
    #     """
    #     Create pdf from images
    #     """
    #     if not os.path.exists(f"{self.path}/chapter {chapter}"):
    #         print(f"No images found for chapter {chapter}")
    #         # print("No images found")
    #         return "error"
    #     img_ext = [".jpg", ".jpeg", ".png", ".gif"]
    #     fnames = [i for i in os.listdir(os.path.join(self.path, f"chapter {chapter}")) if os.path.splitext(i)[1].lower() in img_ext]
    #     # print("Filenames before sorting: ", fnames)
    #     fnames.sort(key=lambda x: int(re.search(r'\d+', x).group()))  # sort by number in filename
    #     # if delete_page_0 is True, remove first page
    #     # print("Sorted filenames: ", fnames)
    #     # print("argument delete_page_0: ", delete_page_0)
    #     if delete_page_0 and len(fnames) > 0:
    #         # print("Deleting first page...")
    #         fnames = fnames[1:]  # remove first page
    #     # fnames = fnames[:4]   # limit to first 4 images for testing
    #     # print("Filenames: ", fnames)
    #     if not fnames:
    #         print(f"No images found in {self.path}/chapter {chapter}")
    #         return "error"
    #     # sort filenames
    #     # print(f"Total: {len(fnames)} images found in chapter {chapter}")
    #     if not os.path.exists(self.path+"/chapter "+str(chapter)):
    #         print("No img directory found")
    #         return "error"
    #     # create pdf from images
    #     try:
    #         imgs = [Image.open(os.path.join(self.path+"/chapter "+str(chapter), i)) for i in fnames]
    #         # print("Total images: ", len(imgs))
    #         ImageFile.LOAD_TRUNCATED_IMAGES = True  # allow truncated images
    #         imgs[0].save(f"{self.path}/chapter {str(chapter)}.pdf", "PDF", save_all=True, append_images=imgs[1:], resolution=100.0) # , quality=95)
    #         # print("Saved chapter "+ str(chapter))
    #         # os.rmdir(f"{self.path}/chapter {str(chapter)}")  # remove img directory after creating pdf
    #         import shutil

    #         # Remove directory and all its contents
    #         shutil.rmtree(f"{self.path}/chapter {str(chapter)}")
    #         # print(f"Removed chapter {chapter} directory after creating pdf")
    #     except Exception as e:
    #         print("Error creating pdf: ", e)
    #         return "error"
    #     return "pdf created"


    def create_cbz(self, chapter, delete_page_0=False):
        """
        Create cbz from images
        """
        if not os.path.exists(f"{self.path}/chapter {chapter}"):
            print(f"No images found for chapter {chapter}")
            return "error"
        img_ext = [".jpg", ".jpeg", ".png", ".gif"]
        fnames = [i for i in os.listdir(os.path.join(self.path, f"chapter {chapter}")) if os.path.splitext(i)[1].lower() in img_ext]
        # print("Filenames before sorting: ", fnames)
        fnames.sort(key=lambda x: int(re.search(r'\d+', x).group()))  # sort by number in filename
        # if delete_page_0 is True, remove first page
        # print("Sorted filenames: ", fnames)
        # print("argument delete_page_0: ", delete_page_0)
        if delete_page_0 and len(fnames) > 0:
            # print("Deleting first page...")
            fnames = fnames[1:]
        # fnames = fnames[:4]   # limit to first 4 images for testing
        # print("Filenames: ", fnames)
        if not fnames:
            print(f"No images found in {self.path}/chapter {chapter}")
            return "error"
        # sort filenames
        # print(f"Total: {len(fnames)} images found in chapter {chapter}")
        if not os.path.exists(self.path+"/chapter "+str(chapter)):
            print("No img directory found")
            return "error"
        # create cbz from images
        try:
            with zipfile.ZipFile(f"{self.path}/chapter {str(chapter)}.cbz", "w") as cbz:
                for img in fnames:
                    cbz.write(os.path.join(self.path, f"chapter {chapter}", img), img)
            # print("Saved chapter "+ str(chapter))
        except Exception as e:
            print("Error creating cbz: ", e)
            return "error"
        
        # remove img directory after creating cbz
        import shutil
        shutil.rmtree(f"{self.path}/chapter {str(chapter)}")
        # print(f"Removed chapter {chapter} directory after creating cbz")
        return "cbz created"


    def get_all_chapter(self, delete_page_0=False, cbz=True):
        """
        Get all chapter data
        """
        chapter_links = self.get_all_chapter_link()
        if chapter_links == "error":
            print("Error getting all chapter links")
            return "error"
        chapter_track = 0
        for i in tqdm.tqdm(range(len(chapter_links)), desc="Processing chapters", unit="chapter"):
            # print(f"Getting chapter data from link: {chapter_links[i]}")
            chapter_num, img_links = self.get_chapter_data(chapter_links[i])
            # self.get_chapter_images(img_links, i+1)  # i+1 because chapter starts from 1
            # self.create_pdf(i+1, delete_page_0)  # create pdf for each chapter
            self.get_chapter_images(img_links, chapter_num)
            if cbz:
                # print("Creating cbz for chapter ", chapter_num)
                self.create_cbz(chapter_num, delete_page_0)  # create cbz for each chapter
            else:
                # print("Creating pdf for chapter ", chapter_num)
                self.create_pdf(chapter_num, delete_page_0)  # create pdf for each chapter
            chapter_track += 1
            time.sleep(3)  # sleep for 5 seconds to avoid being blocked
        print(f"Total chapters retrieved: {chapter_track}")
        print("All chapters data retrieved")
        return "all chapters"


    def get_chapter_from_range(self, start_chapter, end_chapter, delete_page_0=False, cbz=True):
        """
        Get chapter data from range
        """
        if start_chapter > end_chapter:
            print("Start chapter must be less than end chapter")
            return "error"
        chapter_track = 0
        for i in tqdm.tqdm(range(start_chapter, end_chapter + 1), desc="Processing chapters", unit="chapter"):
            # print(f"Getting chapter {i} data...")
            chapter_num, img_links = self.get_chapter_data(i)
            # self.get_chapter_images(img_links, i)
            # self.create_pdf(i, delete_page_0)  # create pdf for each chapter
            self.get_chapter_images(img_links, chapter_num)
            if cbz:
                # print("Creating cbz for chapter ", chapter_num)
                self.create_cbz(chapter_num, delete_page_0)  # create cbz for each chapter
            else:
                # print("Creating pdf for chapter ", chapter_num)
                self.create_pdf(chapter_num, delete_page_0)  # create pdf for each
            # self.create_cbz(chapter_num, delete_page_0)  # create cbz for each chapter
            chapter_track += 1
            time.sleep(3)  # sleep for 5 seconds to avoid being blocked
        print(f"Total chapters retrieved: {chapter_track} / {end_chapter - start_chapter + 1}")
        return "chapter range"

    
    def get_chapter(self, chapter, delete_page_0=False, cbz=True):
        """
        Get chapter data
        """
        # print("Argument delete_page_0 of get_chapter: ", delete_page_0)
        if isinstance(chapter, int):
            print(f"Getting chapter {chapter} data...")
            chapter_num, img_links = self.get_chapter_data(chapter)
            # self.get_chapter_images(img_links, chapter)
            # self.create_pdf(chapter, delete_page_0)
            self.get_chapter_images(img_links, chapter_num)
            # self.create_pdf(chapter_num, delete_page_0)
            self.create_cbz(chapter_num, delete_page_0)
            return "chapter"
        elif isinstance(chapter, str):
            print(f"Getting chapter data from link: {chapter}")
            chapter_num, img_links = self.get_chapter_data(chapter)
            # self.get_chapter_images(img_links, chapter)
            # self.create_pdf(chapter, delete_page_0)
            self.get_chapter_images(img_links, chapter_num)
            if  cbz:
                print("Creating cbz for chapter ", chapter_num)
                self.create_cbz(chapter_num, delete_page_0)  # create cbz for each chapter
            else:
                print("Creating pdf for chapter ", chapter_num)
                self.create_pdf(chapter_num, delete_page_0)
            # self.create_pdf(chapter_num, delete_page_0)
            # self.create_cbz(chapter_num, delete_page_0)
            return "chapter link"
        else:
            print("Chapter must be int or str link")
            return "error"


def control_crawler(crawler, action, *args):
    """
    Control the crawler with different actions
    """
    print(f"Action: {action}, Args: {args}")
    if action == "get_all_chapter":
        return crawler.get_all_chapter(*args)
    elif action == "get_chapter_from_range":
        return crawler.get_chapter_from_range(*args)
    elif action == "get_chapter":
        return crawler.get_chapter(*args)
    else:
        print("Invalid action")
        
        return "error"
    
# Example usage
    


if __name__ == "__main__":
    # link = "https://truyenqqgo.com/truyen-tranh/ba-xa-nha-toi-den-tu-ngan-nam-truoc-16030"
    # crawler = QQCrawler(link)
    ###
    # print(crawler.get_referrer())
    ###
    # print(crawler.get_all_chapter_link())
    ####
    # print(crawler.get_chapter_data(2))
    ###
    # crawler.img_links = ['https://s135.hinhhinh.com/16030/2/0.jpg?gt=hdfgdfg', 
    #                   'https://s135.hinhhinh.com/16030/2/1.jpg?gt=hdfgdfg', 
    #                   'https://s135.hinhhinh.com/16030/2/2.jpg?gt=hdfgdfg']
    # print(crawler.get_chapter_images())
    ###
    # print(crawler.create_pdf(chapter=2))
    
    cont = True
    while cont:
        try:
            print("Welcome to QQ Crawler")
            link = input("Enter comic link: ")
            crawler = QQCrawler(link)
            action = input("Enter action (get_all_chapter, get_chapter_from_range, get_chapter): ")
            delete_page_0 = input("Delete first page? (y/n): ").strip().lower() == "y"
            print("Delete first page: ", delete_page_0)
            
            if delete_page_0:
                print("First page WILL be deleted from pdf")
            else:
                print("First page will NOT be deleted from pdf")

            cbz = input("Create cbz instead of pdf? (y/n) (recommend cbz for manga, pdf for non-manga): ").strip().lower() == "y"

            if action == "get_all_chapter":
                control_crawler(crawler, action, delete_page_0, cbz)
            elif action == "get_chapter_from_range":
                start_chapter = int(input("Enter start chapter: "))
                end_chapter = int(input("Enter end chapter: "))
                control_crawler(crawler, action, start_chapter, end_chapter, delete_page_0, cbz)
            elif action == "get_chapter":
                chapter = input("Enter chapter (int or str link): ")
                if chapter.isdigit():
                    chapter = int(chapter)
                else:
                    chapter = str(chapter)
                control_crawler(crawler, action, chapter, delete_page_0, cbz)
            else:
                print("Invalid action")
                continue
        except Exception as e:
            print("An error occurred: ", e)
            print("Please try again.")
            continue
        
        cont = input("Do you want to continue crawling? (y/n): ").strip().lower()=="y"

    # crawler.get_all_chapter()
    # crawler.get_chapter_from_range(1, 2)
    # crawler.get_chapter(1)
    # crawler.get_chapter("https://truyenqqgo.com/truyen-tranh/ba-xa-nha-toi-den-tu-ngan-nam-truoc-16030-chap-2.html")