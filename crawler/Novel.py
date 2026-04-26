from utils.novel import *


class NovelCrawler:
    def update_log(self, message, new=False):
        log_file = os.path.join(self.output_dir, "logs", "crawl_log.txt")
        if not os.path.exists(os.path.dirname(log_file)):
            os.makedirs(os.path.dirname(log_file))
        if not os.path.exists(log_file):
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("Novel Crawler Log\n\n")
        if new:
            # delete old log and start new log file
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("Novel Crawler Log\n\n")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n\n")


    def __init__(self, url=None, output_dir=None, sleep_time=1000, keep_logged_in=False, driver=False):
        self.url = url
        self.base_url = extract_base_url(url)
        # print("Base URL:", self.base_url)
        self.output_dir = output_dir + "outputs/Novel/" if output_dir else "outputs/Novel/"
        self.sleep_time = sleep_time / 1000  # Convert milliseconds to seconds
        self.keep_logged_in = keep_logged_in
        self.driver = None
        if driver:
            self.driver = webdriver.Chrome()  # or webdriver.Firefox(), etc. Make sure to have the appropriate driver installed

        if url:
            data = pd.read_csv("data/aliases.csv")
            # print("Data:\n", data)
            # print("\n\n")
            # site_name = data.loc[data['site'] == self.base_url, 'name'].values
            site = self.base_url.split("//")[-1].split("/")[0].replace("www.", "")
            # print("Site:", site)
            # base_url_ = self.base_url.split("//")[-1].replace("www.", "")
            # site_name = data[data['site'] == site]['name'].values[0]
            # self.site_name = data[data['site'] == site]['name'].values[0]
            idx = data.index[data['site'] == site]
            # print("Site index:", type(idx[0]))
            self.site_name = data.at[idx[0], 'name'] if not idx.empty else None
            # print("Site name:", self.site_name)
            with open(f"data/formats/{self.site_name}.json", 'r', encoding='utf-8') as f:
                self.format_data = json.load(f)


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
            print("Creating output directory at:", self.output_dir)
            os.makedirs(self.output_dir)



    def check_login_btn(self, html_content):
        soup = BeautifulSoup(html_content, 'html.parser')
        # is_log_in_btn = soup.find(class_=self.format_data['login']['login_btn']['class_']) is None
        # print("Checking login button...")
        try:
            is_log_in_btn = soup.find(class_=self.format_data['login']['login_btn']['class_']) is not None
        except Exception as e:
            is_log_in_btn = False
            # print("Error checking login button:", e)
        self.update_log(f"Login button {'found' if is_log_in_btn else 'not found (meaning: logged in)'} on the page.")
        return is_log_in_btn
    
    def login_to_site(self, site_name, html_content):
        login_btn = self.format_data['login']['login_btn']
        user_field = self.format_data['login']['username']
        password_field = self.format_data['login']['password']
        submit_btn = self.format_data['login']['submit_btn']
        logged_in = login(html_content, self.driver, log_btn_args=login_btn, user_field_args=user_field, 
                       pass_field_args=password_field, submit_btn_args=submit_btn, credential_name=site_name,
                       anchor_class=self.format_data["vol_group"]["vol_section"]["class_"])
        # page_log_in = self.driver.page_source
        self.site_name = site_name
        # with open("test_login.html", "w", encoding="utf-8") as f:
        #     f.write(page_log_in)
        self.update_log(f"Login {'successful' if logged_in else 'failed (meaning: logged in)'} for site: {site_name}")
        return logged_in

    
    def get_all_info(self, custom_volume_list=None, info_url=None):
        # custom_volume_list: list of volume links to crawl instead of extracting from page
        if custom_volume_list and info_url:
            self.url = info_url

        page_content = get_page_content(self.driver, self.url) if not info_url else get_page_content(self.driver, info_url)

        # get novel title
        title_args = {k: v for k, v in self.format_data['title'].items() if v!=""}
        self.title = get_title(page_content, **title_args)
        self.output_dir = os.path.join(self.output_dir, self.title)
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
            print("Output directory:", self.output_dir)
            self.update_log(f"Output directory created at: {self.output_dir}")
        self.novel_info['title'] = self.title
        self.update_log(f"Extracted novel title: {self.title}", new=True)

        if self.format_data.get('login', None) and self.keep_logged_in:
            self.login_to_site(self.site_name, self.driver.page_source)
        soup = BeautifulSoup(self.driver.page_source, 'html.parser') if self.keep_logged_in else BeautifulSoup(page_content, 'html.parser')
        html_content = soup.prettify()
        # print("Fetching novel information from:", self.url)
        # print("Page content fetched.")

        # html_content = get_page_content(self.driver, self.url)
        print("Fetching novel information from:", self.url)
        
        # print(BeautifulSoup(html_content, 'html.parser').prettify())
        if not html_content:
            self.update_log(f"Failed to retrieve page content from: {self.url}")
            raise Exception("Failed to retrieve page content.")

        print("Page content fetched.")
        self.update_log(f"Fetched novel information page: {self.url}")

        # get cover image
        cover_args = {k: v for k, v in self.format_data['cover'].items() if v!=""}
        referrer = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
        cover_image_url = get_cover_image_element(html_content, 
                                                  output_dir=self.output_dir+"/img", ext="jpg",
                                                  img_name="cover", img_referrer=referrer, cover_args=cover_args, update_log=self.update_log)
        self.novel_info['cover_image'] = cover_image_url
        
        self.update_log(f"Extracted cover image URL: {cover_image_url}")
        
        # get genres
        genres_args = {k: v for k, v in self.format_data['genre'].items() if v!=""}
        self.novel_info['genres'] = get_genres(self.driver, html_content, **genres_args)
        
        self.update_log(f"Extracted genres: {self.novel_info['genres']}")

        holders = {k: v for k, v in self.format_data['other_info']['holder'].items() if v!=""}
        values = {k: v for k, v in self.format_data['other_info']['value'].items() if v!=""}
        other_info = get_all_other_novel_info(html_content, holders=holders, values=values)
        
        self.update_log(f"Extracted other info: {other_info}")
        
        for key, value in other_info.items():
            if key.lower() == 'tác giả' or key.lower() == 'author':
                self.novel_info['author'] = value
            # can add more info extraction here if needed
            else:
                self.novel_info["other_info"][key] = value
                # self.novel_info[key] = value

        description_args = {k: v for k, v in self.format_data['description'].items() if v!=""}
        description = get_description(self.driver, html_content, **description_args)
        self.novel_info['description'] = description

        self.update_log(f"Extracted description: {description[:100]}...")  # Log the first 100 characters of the description

        if custom_volume_list:
            self.update_log(f"Using custom volume list with {len(custom_volume_list)} volumes.")
            volume_links = custom_volume_list
            
            vol_page = self.format_data['vol_page']
            vol_title = {k: v for k, v in vol_page['vol_title'].items() if v!=""}
            if 'vol_cover' in vol_page:
                vol_cover = {k: v for k, v in vol_page['vol_cover'].items() if v!=""}
                vol_cover['output_dir'] = self.output_dir+"/img"
                vol_cover['img_referrer'] = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
            else:
                vol_cover = {}
            vol_chap = {k: v for k, v in vol_page['chapter_list'].items() if v!=""}
            volumes = []
            for i, link in enumerate(volume_links):
                if vol_cover:
                    vol_cover['img_name'] = f"vol_{i+1}_cover"
                    # self.update_log(f"Found volume cover for volume {i+1}")
                # print(f"vol cover args for custom volumes: {vol_cover}")
                vol_info = get_volume_info_from_link(self.driver, base_url=self.base_url,
                                                    vol_url=link, 
                                                    title_args=vol_title,
                                                    cover_args=vol_cover,
                                                    chap_list_args=vol_chap,
                                                    update_log=self.update_log)
                volumes.append(vol_info)
                self.update_log(f"Extracted volume info from link: {link} with title: {vol_info['title']}")
                time.sleep(1)  # sleep between volume requests
        else:
            self.update_log("Using automatic volume extraction from the main page.")
            html_content = get_page_content(self.driver, self.url)

            group = self.format_data['vol_group']
            vol_section = {k: v for k, v in group['vol_section'].items() if v!=""}
            vol_title = {k: v for k, v in group['vol_title'].items() if v!=""}
            vol_cover = {k: v for k, v in group['vol_cover'].items() if v!=""}
            vol_cover['output_dir'] = self.output_dir+"/img"
            vol_chap = {k: v for k, v in group['chapter_list'].items() if v!=""}
            volumes = get_all_volume(html_content, vol_sect=vol_section, 
                                    vol_title=vol_title, 
                                    vol_cover=vol_cover, 
                                    vol_chap=vol_chap, 
                                    base_url=self.base_url,
                                    update_log=self.update_log)
            self.update_log(f"Extracted information for {len(volumes)} volumes from the main page.")
        print("Got all novel information.")
        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({"info" : self.novel_info, "volumes": volumes}, f, ensure_ascii=False, indent=4)
        return self.novel_info, volumes


    def crawl_chapter_(self, chapter_url, img_output_dir=None, img_prefix=""):
        try:
            logged_in = not self.keep_logged_in  # If we don't need to keep logged in, we can skip login check
            if self.driver and self.keep_logged_in:
                self.driver.get(chapter_url)
                self.driver.implicitly_wait(10)
                # logged_in = True

            # if self.format_data.get('login', None):
                logged_in = self.check_login_btn(self.driver.page_source)
            if not logged_in:
                self.login_to_site(self.site_name, self.driver.page_source)
            chapter_content = self.driver.page_source if self.keep_logged_in else get_page_content(self.driver, chapter_url)
        except Exception as e:
            self.update_log(f"Error fetching chapter content from {chapter_url}: {e}")
            print(f"Error fetching chapter content from {chapter_url}: {e}")
            print("Retrying...")
            for _attempt in range(5):
                time.sleep(2)  # wait before retrying
                if self.driver and self.keep_logged_in:
                    self.driver.get(chapter_url)
                    self.driver.implicitly_wait(10)
                    logged_in = self.check_login_btn(self.driver.page_source) if self.format_data.get('login', None) else True
                    if not logged_in:
                        self.login_to_site(self.site_name, self.driver.page_source)
                chapter_content = self.driver.page_source if self.keep_logged_in else get_page_content(None, chapter_url)
                if chapter_content:
                    break
            else:
                print(f"Failed to retrieve chapter content from {chapter_url} after retries.")
                return None
        if not chapter_content:
            self.update_log(f"Failed to retrieve chapter content from {chapter_url}")
            print(f"Failed to retrieve chapter content from {chapter_url}")
            return None
        chapter_prop = self.format_data['chapter']
        chapter_title = get_chapter_title(chapter_content, chapter_prop['title'], update_log=self.update_log)
        chapter_body = get_chapter_content(chapter_content, chapter_prop['content'], update_log=self.update_log)
        soup = BeautifulSoup(chapter_body, 'html.parser')

        self.update_log(f"Extracted chapter title: {chapter_title} and content (text only) from URL: {chapter_url}")

        img_args = {k: v for k, v in chapter_prop['image'].items() if k not in ['delete'] and v!=""}
        img_src = get_image_urls(chapter_body, self.base_url, **img_args)
        imgs = soup.find_all(**{k: v for k, v in img_args.items() if k not in ['other_attr'] and v!=""})
        # print(f"image to delete: {chapter_prop['image']['delete']}, total images found: {len(img_src)}")
        if chapter_prop['image']['delete']:
            for i in range(abs(chapter_prop['image']['delete'])):
                imgs[-(i+1)].decompose()
                img_src.pop(-1)
        chapter_body = soup.prettify()
        self.update_log(f"Deleted {chapter_prop['image']['delete']} images in chapter content (set as ad).")

        chapter_img_folder = None
        for i, img_url in enumerate(img_src):
            img_name = f"{img_prefix}_img{i+1}"
            local_img_path = download_image(img_url, output_dir=img_output_dir, ext="jpg", name=img_name,
                                        img_referrer=self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False, 
                                        update_log=self.update_log)
            new_chapter_body = re.sub(rf'{img_url}', local_img_path, chapter_body)
            
            chapter_body = new_chapter_body
            
            chapter_img_folder = os.path.dirname(local_img_path)

            # self.update_log(f"Downloaded image from {img_url} to {local_img_path} and updated chapter content.")

        self.update_log(f"Finished processing chapter from URL: {chapter_url} with title: {chapter_title}")
        return {
            "chapter_title": chapter_title,
            "chapter_content": chapter_body,
            "chapter_img_folder": chapter_img_folder
        }

    
    def crawl(self, custom_volume_list=None, info_url=None):
        novel_info, volumes = self.get_all_info(custom_volume_list=custom_volume_list, info_url=info_url)
        for idx, vol in enumerate(volumes):
            all_chapters = []
            self.update_log(f"Starting to crawl Volume {idx+1}: {vol['title']} with {len(vol['chapter_links'])} chapters.")
            print(f"Crawling Volume {idx+1}: {vol['title']}")
           
            for jdx, chap_url in tqdm.tqdm(enumerate(vol['chapter_links']),
                                           total=len(vol['chapter_links']),
                                           desc=f"Crawling Volume {idx+1} Chapters", unit="chapter"):
                # print(f"Crawling Volume {idx+1} - Chapter {jdx+1}: {chap_url}")
                chapter_data = None
                try:
                    chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
                                                      img_prefix=f"vol{idx+1}_chap{jdx+1}")
                except Exception as e:
                    self.update_log(f"Error crawling chapter {chap_url}: {e}")
                    print(f"Error crawling chapter {chap_url}: {e}")
                    print("Retrying...")
                    for _attempt in range(5):
                        time.sleep(2)  # wait before retrying
                        try:
                            chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
                                                              img_prefix=f"vol{idx+1}_chap{jdx+1}")
                            if chapter_data:
                                break
                        except Exception as e:
                            print(f"Retry {_attempt+1} failed: {e}")
                            if _attempt == 4:
                                source = get_page_content(self.driver, chap_url) if self.driver else None
                                with open(self.output_dir + f"/logs/debug_chapter_{jdx+1}.html", "w", encoding="utf-8") as f:
                                    f.write(BeautifulSoup(self.driver.page_source, "html.parser").prettify())
                                self.update_log(f"Failed to crawl chapter {chap_url} after retries. Page source for debugging stored.")
                        except KeyboardInterrupt:
                            print("Crawling interrupted by user.")
                            with open(self.output_dir + f"/logs/debug_chapter_{jdx+1}.html", "w", encoding="utf-8") as f:
                                f.write(BeautifulSoup(chapter_data, "html.parser").prettify())
                            self.update_log("Crawling interrupted by user. Page source for debugging stored.")
                            print("Page source for debugging stored")
                            return
                    # else:
                    #     print(f"Failed to crawl chapter {chap_url} after retries.")
                    #     print("Pausing for 60 seconds...")
                    #     print("Page source for debugging:")
                    #     # print(BeautifulSoup(self.driver.page_source, "html.parser").prettify())
                    #     with open(self.output_dir + "/logs/debug_chapter.html", "w", encoding="utf-8") as f:
                    #         f.write(BeautifulSoup(self.driver.page_source, "html.parser").prettify())
                    #     self.update_log(f"Failed to crawl chapter {chap_url} after retries. Page source for debugging stored.")
                all_chapters.append(chapter_data) if chapter_data else {}
                time.sleep(self.sleep_time)
            vol['chapter_contents'] = all_chapters

        info = {"info" : novel_info}
        all_vol = {"volumes": volumes}

        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
        self.update_log(f"Crawling all chapters completed and data saved.")
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
        self.update_log(f"Crawling of selected chapter range ({start_chapter} to {end_chapter}) completed and data saved.")
        print("Crawling of selected chapter range completed and data saved.")


    def crawl_chapter(self, chapter_url, info_url=None):
        novel_info, volumes = self.get_all_info(info_url=info_url) if info_url else self.get_all_info()
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
        self.update_log(f"Crawling of single chapter from URL: {chapter_url} completed and data saved.")
        print("Crawling of single chapter completed and data saved.")
    
    def make_epub(self, novel_info_path=None):
        # make epub from crawled data in novel_info.json
        # self.output_dir = os.path.join(self.output_dir, "Hội chứng muốn sống bình an tại dị giới")
        self.output_dir = novel_info_path if novel_info_path else self.output_dir
        novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
        if not os.path.exists(novel_info_path):
            self.update_log("Novel information file not found.")
            print("Novel information file not found. Please run the crawler first.")
            return
        with open(novel_info_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        novel_info = data['info']
        volumes = data['volumes']

        # make volumes epub elements
        volume_list = []
        for idx, vol in enumerate(volumes):
            self.update_log(f"Preparing Volume {idx+1} for EPUB: {vol['title']}")
            print(f"Preparing Volume: {vol['title']}")
            chapter_elements = []
            for chap_idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
                                     desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
                c = make_chapter_epub(chapter, title_added=(f"Vol {idx+1}", ""), idx=chap_idx+1, update_log=self.update_log)
                chapter_elements.append(c)
            v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
            self.update_log(f"Volume {idx+1} prepared for EPUB with title: {vol['title']}")
            volume_list.append((v_img, v, c_lst))
        # create epub
        make_book_epub(novel_info, volume_list, ("",""), output_path=self.output_dir, ebook_name=novel_info['title'] + ".epub")
        self.update_log(f"EPUB creation completed for novel: {novel_info['title']} with {len(volumes)} volumes.")
        

    
    def make_volume_epub(self, novel_info_path=None):
        self.output_dir = novel_info_path if novel_info_path else self.output_dir
        novel_info_path = os.path.join(self.output_dir, 'novel_info.json')
        if not os.path.exists(novel_info_path):
            self.update_log("Novel information file not found.")
            print("Novel information file not found. Please run the crawler first.")
            return
        with open(novel_info_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        novel_info = data['info']
        volumes = data['volumes']

        for i, vol in enumerate(volumes):
            print(f"Preparing Volume: {vol['title']}")
            chapter_elements = []
            for chap_idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
                                     desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
                c = make_chapter_epub(chapter, title_added=("", ""), idx=chap_idx+1, update_log=self.update_log)
                chapter_elements.append(c)
            v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
            # create epub for each volume
            self.update_log(f"Creating EPUB for Volume: {vol['title']}")
            make_book_epub(novel_info, [(v_img, v, c_lst)], (f"vol {i+1}",""), output_path=self.output_dir, ebook_name=novel_info['title'] + f" vol {i+1} - {vol['title']}.epub")
            self.update_log(f"EPUB creation completed for Volume: {vol['title']} with {len(vol['chapter_contents'])} chapters.")






class NovelRequests(NovelCrawler):
    # def __init__(self, url, output_dir, sleep_time=1000):
    #     self.url = url
    #     self.base_url = extract_base_url(url)
    #     # print("Base URL:", self.base_url)
    #     self.output_dir = output_dir + "outputs/Novel/" if output_dir else "outputs/Novel/"
    #     self.sleep_time = sleep_time / 1000  # Convert milliseconds to seconds

    #     data = pd.read_csv("data/aliases.csv")
    #     # print("Data:\n", data)
    #     # print("\n\n")
    #     # site_name = data.loc[data['site'] == self.base_url, 'name'].values
    #     site = self.base_url.split("//")[-1].split("/")[0]
    #     # print("Site:", site)
    #     site_name = data[data['site'] == site]['name'].values[0]
    #     # print("Site name:", site_name)
    #     with open(f"data/formats/{site_name}.json", 'r', encoding='utf-8') as f:
    #         self.format_data = json.load(f)


    #     self.title = None
    #     self.novel_info = {
    #         "title": None,
    #         "author": None,
    #         "other_info": {},
    #         "cover_image": None,
    #         "num_chapters": None,
    #         "description": None,
    #         "genres": [],
    #         "novel_url": self.url,
    #         "start_chapter": None,
    #         "end_chapter": None,
    #         "chapter_links": [],
    #         }
        
    #     if not os.path.exists(self.output_dir):
    #         os.makedirs(self.output_dir)

    def get_all_info(self, custom_volume_list=None, info_url=None):
        # custom_volume_list: list of volume links to crawl instead of extracting from page
        if custom_volume_list and info_url:
            self.url = info_url
        html_content = get_page_content(self.url)
        print("Fetching novel information from:", self.url)
        print("Page content fetched.")
        # print(BeautifulSoup(html_content, 'html.parser').prettify())
        if not html_content:
            print("Failed to retrieve page content.")
            return
        


        # get novel title
        title_args = {k: v for k, v in self.format_data['title'].items() if v!=""}
        self.title = get_title(html_content, 
                               **title_args
                               )
        self.output_dir = os.path.join(self.output_dir, self.title)
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        self.novel_info['title'] = self.title

        # get cover image
        cover_args = {k: v for k, v in self.format_data['cover'].items() if v!=""}
        referrer = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
        cover_image_url = get_cover_image_element(html_content,
                                                  output_dir=self.output_dir+"/img", ext="jpg",
                                                  img_name="cover", img_referrer=referrer, **cover_args)
        self.novel_info['cover_image'] = cover_image_url

        # get genres
        genre_args = {k: v for k, v in self.format_data['genre'].items() if v!=""}
        genres = get_genres(None, html_content, **genre_args)
        self.novel_info['genres'] = genres


        holders = {k: v for k, v in self.format_data['other_info']['holder'].items() if v!=""}
        values = {k: v for k, v in self.format_data['other_info']['value'].items() if v!=""}
        other_info = get_all_other_novel_info(html_content, holders=holders, values=values)
        for key, value in other_info.items():
            if key.lower() == 'tác giả' or key.lower() == 'author':
                self.novel_info['author'] = value
            # can add more info extraction here if needed
            else:
                self.novel_info["other_info"][key] = value
                # self.novel_info[key] = value

        
        description_args = {k: v for k, v in self.format_data['description'].items() if v!=""}
        description = get_description(None, html_content, **description_args)
        self.novel_info['description'] = description

        if custom_volume_list:
            volume_links = custom_volume_list
            
            vol_page = self.format_data['vol_page']
            vol_title = {k: v for k, v in vol_page['vol_title'].items() if v!=""}
            if 'vol_cover' in vol_page:
                vol_cover = {k: v for k, v in vol_page['vol_cover'].items() if v!=""}
                vol_cover['output_dir'] = self.output_dir+"/img"
                vol_cover['img_referrer'] = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
            else:
                vol_cover = {}
            vol_chap = {k: v for k, v in vol_page['chapter_list'].items() if v!=""}
            volumes = []
            for i, link in enumerate(volume_links):
                if vol_cover:
                    vol_cover['img_name'] = f"vol_{i+1}_cover"
                vol_info = get_volume_info_from_link(base_url=self.base_url, vol_url=link, 
                                                    title_args=vol_title,
                                                    cover_args=vol_cover,
                                                    chap_list_args=vol_chap)
                volumes.append(vol_info)
                time.sleep(1)  # sleep between volume requests
        else:
            group = self.format_data['vol_group']
            vol_section = {k: v for k, v in group['vol_section'].items() if v!=""}
            vol_title = {k: v for k, v in group['vol_title'].items() if v!=""}
            vol_cover = {k: v for k, v in group['vol_cover'].items() if v!=""}
            vol_cover['output_dir'] = self.output_dir+"/img"
            vol_chap = {k: v for k, v in group['chapter_list'].items() if v!=""}
            volumes = get_all_volume(html_content, vol_sect=vol_section,
                                    vol_title=vol_title,
                                    vol_cover=vol_cover,
                                    vol_chap=vol_chap,
                                  base_url=self.base_url)

       
        print("Got all novel information.")
        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({"info" : self.novel_info, "volumes": volumes}, f, ensure_ascii=False, indent=4)
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
        chapter_prop = self.format_data['chapter']
        try:
            chapter_title = get_chapter_title(chapter_content, chapter_prop['title'], update_log=self.update_log)
        except Exception as e:
            print(f"Error extracting chapter title from {chapter_url}: {e}")
            print("Retrying...")
            for _attempt in range(5):
                time.sleep(2)  # wait before retrying
                chapter_content = get_page_content(chapter_url)
                chapter_title = get_chapter_title(chapter_content, chapter_prop['title'], update_log=self.update_log)
                if chapter_title:
                    break
            else:
                print(f"Failed to extract chapter title from {chapter_url} after retries.")
                chapter_title = "No Title"
        chapter_body = get_chapter_content(chapter_content, **chapter_prop['content'])
        soup = BeautifulSoup(chapter_body, 'html.parser')
       
        img_args = {k: v for k, v in chapter_prop['image'].items() if k not in ['delete'] and v!=""}
        img_src = get_image_urls(chapter_body, self.base_url, **img_args)
        imgs = soup.find_all(**{k: v for k, v in img_args.items() if k not in ['other_attr'] and v!=""})
        # print("Images found: ", len(img_src))
        # print("Pre download Images URLs: ", imgs)
        if chapter_prop['image']['delete']:
            for i in range(abs(chapter_prop['image']['delete'])):
                # img_ele[-1].decompose()  # remove unwanted img tags (ads)
                # print(i)
                imgs[-(i+1)].decompose()
                img_src.pop(-1)
                # print("Images left after deletion: ", len(img_src))
        # print("Images to download: ", len(img_src))
        # print("Image elements: ", imgs)
        # imgs_ = soup.find_all(**{k: v for k, v in img_args.items() if k not in ['other_attr'] and v!=""})
        # print("Images after deletion: ", len(imgs_))
        chapter_body = soup.prettify()
        
        chapter_img_folder = None
        for i, img_url in enumerate(img_src):
            img_name = f"{img_prefix}_img{i+1}"
            local_img_path = download_image(img_url, output_dir=img_output_dir, ext="jpg", name=img_name,
                                        img_referrer=self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False)
            new_chapter_body = re.sub(rf'{img_url}', local_img_path, chapter_body)
        
            chapter_body = new_chapter_body
        
            chapter_img_folder = os.path.dirname(local_img_path)
        
        # else:
        #     chapter_img_folder = None
        
        return {
            "chapter_title": chapter_title,
            "chapter_content": chapter_body,
            "chapter_img_folder": chapter_img_folder
        }

    

    def crawl(self, custom_volume_list=None, info_url=None):
        novel_info, volumes = self.get_all_info(custom_volume_list=custom_volume_list, info_url=info_url)
        for idx, vol in enumerate(volumes):
            all_chapters = []
            print(f"Crawling Volume {idx+1}: {vol['title']}")
           
            for jdx, chap_url in tqdm.tqdm(enumerate(vol['chapter_links']),
                                           total=len(vol['chapter_links']),
                                           desc=f"Crawling Volume {idx+1} Chapters", unit="chapter"):
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
        for idx, vol in enumerate(volumes):
            print(f"Preparing Volume: {vol['title']}")
            chapter_elements = []
            for chap_idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
                                     desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
                c = make_chapter_epub(chapter, title_added=(f"Vol {idx+1}", ""), idx=chap_idx+1)
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
            for chap_idx, chapter in tqdm.tqdm(enumerate(vol['chapter_contents']), 
                                     desc="Processing Chapters", total=len(vol['chapter_contents']), unit="chapters"):
                c = make_chapter_epub(chapter, title_added=("", ""), idx=chap_idx+1)
                chapter_elements.append(c)
            v_img, v, c_lst = make_volume_epub(vol, chapter_elements)
            # create epub for each volume
            make_book_epub(novel_info, [(v_img, v, c_lst)], ("",""), output_path=self.output_dir, ebook_name=novel_info['title'] + f" - {vol['title']}.epub")


class NovelSelenium(NovelCrawler):
    def __init__(self, url, output_dir, sleep_time=1000):
        super().__init__(url, output_dir, sleep_time)
        self.driver = webdriver.Chrome()  # Ensure you have the ChromeDriver installed and in PATH
        # self.driver = uc.Chrome()  # Using undetected-chromedriver
        # print("Initialized Selenium WebDriver for url.")
        # print(f"URL: {self.url}")
        # print(f"Site name: {self.site_name}")

    def get_page_content(self, url):
        # print("getting page content...")
        self.driver.implicitly_wait(100)  # Wait up to 10 seconds for elements to load
        html = ""
        try:
            self.driver.get(url)
            self.driver.find_element(By.CLASS_NAME, self.format_data["cover"]["class_"])  # Ensure the body tag is loaded
            html = self.driver.page_source
        except Exception as e:

            # with SB(uc=True, xvfb=True) as sb:
            #     # 1. Truy cập trang web với cơ chế tự động kết nối lại nếu bị chặn ban đầu
            #     sb.uc_open_with_reconnect(url, reconnect_time=4)
                
            #     # 2. Xử lý xác minh "Verify you are human" (nếu có)
            #     # Phương thức này mô phỏng click chuột thực tế để tránh bị phát hiện
            #     sb.uc_gui_click_captcha()
            #     sb.wait_for_ready_state()
            #     # 3. Đợi một chút để trang tải hoàn tất sau khi xác minh
            #     sb.sleep(2)
            bypass_capcha(self.driver, url)
            html = self.driver.page_source

                # html = sb.driver.page_source
            # html = bypass_capcha_source(url)
            print("Error loading page:", e)
        soup = BeautifulSoup(html, 'html.parser')
        html_content = soup.prettify()
        print("Page content fetched.")
        # with open("test.html", "w", encoding="utf-8") as f:
        #     f.write(html_content)
        # print("Page content: get")
        return html_content

    def check_login_btn(self, html_content):
        soup = BeautifulSoup(html_content, 'html.parser')
        # is_log_in_btn = soup.find(class_=self.format_data['login']['login_btn']['class_']) is None
        # print("Checking login button...")
        try:
            is_log_in_btn = soup.find(class_=self.format_data['login']['login_btn']['class_']) is not None
        except Exception as e:
            is_log_in_btn = False
            # print("Error checking login button:", e)
        return is_log_in_btn
    
    def login_to_site(self, site_name, html_content):
        login_btn = self.format_data['login']['login_btn']
        user_field = self.format_data['login']['username']
        password_field = self.format_data['login']['password']
        submit_btn = self.format_data['login']['submit_btn']
        logged_in = login(html_content, self.driver, log_btn_args=login_btn, user_field_args=user_field, 
                       pass_field_args=password_field, submit_btn_args=submit_btn, credential_name=site_name,
                       anchor_class=self.format_data["vol_group"]["vol_section"]["class_"])
        # page_log_in = self.driver.page_source
        self.site_name = site_name
        # with open("test_login.html", "w", encoding="utf-8") as f:
        #     f.write(page_log_in)
        return logged_in

    def get_all_info(self, custom_volume_list=None, info_url=None):
        self.get_page_content(self.url) if not info_url else self.get_page_content(info_url)
        if self.format_data.get('login', None):
            self.login_to_site(self.site_name, self.driver.page_source)
        soup = BeautifulSoup(self.driver.page_source, 'html.parser')
        html_content = soup.prettify()
        # print("Fetching novel information from:", self.url)
        # print("Page content fetched.")
        if not html_content:
            print("Failed to retrieve page content.")
            return

        # get novel title
        title_args = {k: v for k, v in self.format_data['title'].items() if v!=""}
        # print("Title args:", title_args)
        self.title = get_title(html_content, 
                               **title_args
                               )
        self.output_dir = os.path.join(self.output_dir, self.title)
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir)
        self.novel_info['title'] = self.title

        # get cover image
        cover_args = {k: v for k, v in self.format_data['cover'].items() if v!=""}
        referrer = self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False
        cover_image_url = get_cover_image_element(html_content,
                                                  output_dir=self.output_dir+"/img", ext="jpg",
                                                  img_name="cover", img_referrer=referrer, **cover_args)
        self.novel_info['cover_image'] = cover_image_url

        # get genres
        genre_args = {k: v for k, v in self.format_data['genre'].items() if v!=""}
        genres = get_genres(self.driver, html_content, **genre_args)
        self.novel_info['genres'] = genres

        holders = {k: v for k, v in self.format_data['other_info']['holder'].items() if v!=""}
        values = {k: v for k, v in self.format_data['other_info']['value'].items() if v!=""}
        other_info = get_all_other_novel_info(html_content, holders=holders, values=values)
        for key, value in other_info.items():
            if key.lower() == 'tác giả' or key.lower() == 'author':
                self.novel_info['author'] = value
            # can add more info extraction here if needed
            else:
                self.novel_info["other_info"][key] = value
                # self.novel_info[key] = value

        description_args = {k: v for k, v in self.format_data['description'].items() if v!=""}
        description = get_description(self.driver, html_content, **description_args)
        self.novel_info['description'] = description

        html_content = self.driver.page_source

        group = self.format_data['vol_group']
        vol_section = {k: v for k, v in group['vol_section'].items() if v!=""}
        vol_title = {k: v for k, v in group['vol_title'].items() if v!=""}
        vol_cover = {k: v for k, v in group['vol_cover'].items() if v!=""}
        vol_cover['output_dir'] = self.output_dir+"/img"
        vol_chap = {k: v for k, v in group['chapter_list'].items() if v!=""}
        volumes = get_all_volume(html_content, vol_sect=vol_section,
                                vol_title=vol_title,
                                vol_cover=vol_cover,
                                vol_chap=vol_chap,
                              base_url=self.base_url)

        print("Got all novel information.")
        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({"info" : self.novel_info, "volumes": volumes}, f, ensure_ascii=False, indent=4)
        return self.novel_info, volumes

    def crawl_chapter_(self, chapter_url, img_output_dir=None, img_prefix=""):
        try:
            self.driver.get(chapter_url)
            self.driver.implicitly_wait(10)  # Wait up to 10 seconds for elements to load
            logged_in = True
            if self.format_data.get('login', None):
                logged_in = self.check_login_btn(self.driver.page_source)
            if not logged_in:
                self.login_to_site(self.site_name, self.driver.page_source)
            chapter_content = self.driver.page_source
        except Exception as e:
            print(f"Error fetching chapter content from {chapter_url}: {e}")
            print("Retrying...")
            for _attempt in range(5):
                self.driver.implicitly_wait(10)  # Wait up to 10 seconds for elements to load
                self.driver.get(chapter_url)
                logged_in = self.check_login_btn(self.driver.page_source)
                if not logged_in:
                    self.login_to_site(self.site_name, self.driver.page_source)
                chapter_content = self.driver.page_source
                if chapter_content:
                    break
            else:
                print(f"Failed to retrieve chapter content from {chapter_url} after retries.")
                return None
            chapter_content = self.driver.page_source
        if not chapter_content:
            print(f"Failed to retrieve chapter content from {chapter_url}")
            return None
        chapter_prop = self.format_data['chapter']
        chapter_title = get_chapter_title(chapter_content, **chapter_prop['title'])
        chapter_body = get_chapter_content(chapter_content, **chapter_prop['content'])
        soup = BeautifulSoup(chapter_body, 'html.parser')
       
        img_args = {k: v for k, v in chapter_prop['image'].items() if k not in ['delete'] and v!=""}
        img_src = get_image_urls(chapter_body, self.base_url, **img_args)
        imgs = soup.find_all(**{k: v for k, v in img_args.items() if k not in ['other_attr'] and v!=""})
        # print("Images found: ", len(img_src))
        # print("Pre download Images URLs: ", imgs)
        if "delete" in chapter_prop['image'] and chapter_prop['image']['delete']:
            for i in range(abs(chapter_prop['image']['delete'])):
                # img_ele[-1].decompose()  # remove unwanted img tags (ads)
                # print(i)
                imgs[-i-1].decompose()
                img_src.pop(-1)
                # print("Images left after deletion: ", len(img_src))
        chapter_body = soup.prettify()
        
        chapter_img_folder = None
        for i, img_url in enumerate(img_src):
            img_name = f"{img_prefix}_img{i+1}"
            local_img_path = download_image(img_url, output_dir=img_output_dir, ext="jpg", name=img_name,
                                        img_referrer=self.format_data['img_referrer'] if 'img_referrer' in self.format_data else False)
            new_chapter_body = re.sub(rf'{img_url}', local_img_path, chapter_body)
        
            chapter_body = new_chapter_body
        
            chapter_img_folder = os.path.dirname(local_img_path)
        
        # else:
        #     chapter_img_folder = None
        
        return {
            "chapter_title": chapter_title,
            "chapter_content": chapter_body,
            "chapter_img_folder": chapter_img_folder
        }

    def crawl(self, custom_volume_list=None, info_url=None, keep_logged_in=True):
        novel_info, volumes = self.get_all_info()
        for idx, vol in enumerate(volumes):
            # if idx != 12:
            #     continue
            all_chapters = []
            print(f"Crawling Volume {idx+1}: {vol['title']}")
           
            for jdx, chap_url in tqdm.tqdm(enumerate(vol['chapter_links']),
                                           total=len(vol['chapter_links']),
                                           desc=f"Crawling Volume {idx+1} Chapters", unit="chapter"):
                # print(f"Crawling Volume {idx+1} - Chapter {jdx+1}: {chap_url}")
                chapter_data = None
                try:
                    chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
                                                    img_prefix=f"vol{idx+1}_chap{jdx+1}", keep_logged_in=keep_logged_in)
                except Exception as e:
                    print(f"Error crawling chapter {chap_url}: {e}")
                    print("Retrying...")
                    for _attempt in range(5):
                        time.sleep(2)  # wait before retrying
                        try:
                            chapter_data = self.crawl_chapter_(chap_url, img_output_dir=self.output_dir+"/img", 
                                                            img_prefix=f"vol{idx+1}_chap{jdx+1}", 
                                                            keep_logged_in=keep_logged_in)
                            if chapter_data:
                                break
                        except Exception as e:
                            print(f"Retry {_attempt+1} failed: {e}")
                        except KeyboardInterrupt:
                            print("Crawling interrupted by user.")
                            with open("debug_chapter.html", "w", encoding="utf-8") as f:
                                f.write(BeautifulSoup(self.driver.page_source, "html.parser").prettify())
                            print("Page source for debugging stored")
                            return
                    else:
                        print(f"Failed to crawl chapter {chap_url} after retries.")
                        print("Pausing for 60 seconds...")
                        print("Page source for debugging:")
                        print(BeautifulSoup(self.driver.page_source, "html.parser").prettify())
                        with open("debug_chapter.html", "w", encoding="utf-8") as f:
                            f.write(BeautifulSoup(self.driver.page_source, "html.parser").prettify())
                all_chapters.append(chapter_data) if chapter_data else {}
                time.sleep(self.sleep_time)
            vol['chapter_contents'] = all_chapters

        info = {"info" : novel_info}
        all_vol = {"volumes": volumes}

        with open(os.path.join(self.output_dir, 'novel_info.json'), 'w', encoding='utf-8') as f:
            json.dump({**info, **all_vol}, f, ensure_ascii=False, indent=4)
        print("Crawling completed and data saved.")
    
    def crawl_range(self, start_chapter, end_chapter, custom_volume_list=None, info_url=None, keep_logged_in=True):
        novel_info, volumes = self.get_all_info()
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
                                              img_prefix=f"vol{vol_idx+1}_chap{start_chapter + i}", 
                                              keep_logged_in=keep_logged_in)
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
    
    def crawl_chapter(self, chapter_url, info_url=None, keep_logged_in=True):
        novel_info, volumes = self.get_all_info(info_url=info_url) if info_url else self.get_all_info()
        chapter_data = self.crawl_chapter_(chapter_url, img_output_dir=self.output_dir+"/img", 
                                          img_prefix="single_chap", keep_logged_in=keep_logged_in)
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
    url1 = "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun"
    url = "https://valvrareteam.net/truyen/no-game-no-life-cd23c8d9"

    vol_lst = ["https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t17045-arc-2-vach-tran"]
    vol_lst1 = [
        "https://docln.sbs/truyen/8306-isekai-demo-bunan-ni-ikitai-shoukougun/t12965-arc-1",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t17045-arc-2-vach-tran",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t18598-arc-3-tao-ngo",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t19121-arc-4-ke-sach",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t20030-arc-5-khai-mac",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t20803-arc-6-kich-chien",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t21609-arc-7-lua-chon",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t22375-arc-8-cham-dut",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t23615-arc-9-binh-an",
        "https://docln.sbs/truyen/11662-isekai-demo-bunan-ni-ikitai-shoukougun/t21555-ngoai-truyen"
    ]



    crawler = NovelSelenium(url, output_dir=None, sleep_time=1000)
    page_content = crawler.get_page_content(url)
    print("output dir:", crawler.output_dir)
    logged_in = crawler.login_to_site("valvrareteam", page_content)
    print("Logged in:", logged_in)
    # crawler.get_all_info()
    crawler.crawl()
    # crawler.run()

    # crawler = NovelCrawler(url, output_dir=None, sleep_time=1000)
    # # crawler.get_all_info()
    # crawler.crawl()
    # # crawler.crawl_chapter(chap2)
    # print("output dir:", crawler.output_dir)
    # crawler.make_epub()


def run(**kwargs):
    # print("args:", kwargs)
    db = pd.read_csv("data/aliases.csv")
    print(db)
    url = kwargs.get("novel_url", "")
    print("URL:", url)
    output_dir = kwargs.get("output_dir", None)
    sleep_time = kwargs.get("sleep_time", 1000)
    crawl_type = kwargs.get("crawl_type", "full")  # full, range, single
    # custom_volume_list = kwargs.get("custom_volume_list", None) # list of volume URLs
    crawl_type_args = kwargs.get("crawl_type_args", {})  # dict with args for crawl type
    book_type = kwargs.get("book_type", "all")  #  all, volume
    keep_logged_in = kwargs.get("keep_logged_in", False)  # whether to keep logged in state during crawling (for Selenium-based crawler)
    if not url:
        print("No URL provided.")
        return
    print("Starting crawler for URL:", url)
    base_url = extract_base_url(url)
    print("Base URL:", base_url)
    base_url_ = base_url.split("//")[-1].replace("www.", "")
    site_name = db[db['site'] == base_url_]['name'].values[0]
    class_name = db[db['site'] == base_url_]['crawler_class'].values[0]
    crawler = None
    custom_volume_list = crawl_type_args.get("custom_volume_list", None)
    info_url = crawl_type_args.get("info_url", None)
    start_chap = crawl_type_args.get("start_chapter", None)
    end_chap = crawl_type_args.get("end_chapter", None)
    chap_url = crawl_type_args.get("chapter_url", None)

    # print(f"URL: {url}\noutput_dir: {output_dir}\nsleep_time: {sleep_time}\ncrawl_type: {crawl_type}\ncrawl_type_args: {crawl_type_args}\nbook_type: {book_type}\nkeep_logged_in: {keep_logged_in}")


    if class_name == "NovelSelenium":
        print("Using Selenium-based crawler.")
        # crawler = NovelSelenium(url, output_dir=output_dir, sleep_time=sleep_time)
        # custom_volume_list = None
        crawler = NovelCrawler(url, output_dir=output_dir, sleep_time=sleep_time, keep_logged_in=keep_logged_in, driver=True)
        # info_url = None
    else:
        print("Using Requests-based crawler.")
        # crawler = NovelRequests(url, output_dir=output_dir, sleep_time=sleep_time)
        crawler = NovelCrawler(url, output_dir=output_dir, sleep_time=sleep_time, keep_logged_in=False, driver=False)
    # print("output dir:", crawler.output_dir)


    # crawler.get_page_content(url)

    if crawl_type == "full":
        if custom_volume_list:
            c_list = eval(custom_volume_list) if isinstance(custom_volume_list, str) else custom_volume_list
            crawler.crawl(custom_volume_list=c_list, info_url=info_url)
        else:
            crawler.crawl()
    elif crawl_type == "range":
        start_chap = crawl_type_args.get("start_chapter", 1)
        end_chap = crawl_type_args.get("end_chapter", 1)
        
        crawler.crawl_range(start_chap, end_chap)
    elif crawl_type == "single":
        chap_url = crawl_type_args.get("chapter_url", "")
        if not chap_url:
            print("No chapter URL provided for single chapter crawl.")
            return
        crawler.crawl_chapter(chap_url, info_url=info_url)
    

    if book_type == "all":
        crawler.make_epub()
    elif book_type == "volume":
        crawler.make_volume_epub()

    if crawler.driver:
        crawler.driver.quit()

    return True

def make_epub():
    novel_info_path = input("Enter the path to novel_info.json file: ").strip()
    make_volume = input("Make separate EPUB for each volume? (y/n, default n): ").strip().lower() == "y"
    if not os.path.exists(novel_info_path):
        print("Novel information file not found. Please run the crawler first.")
        return
    crawler = NovelCrawler(url="", output_dir=None, sleep_time=1000)  # Dummy crawler instance to use make_epub method
    if make_volume:
        crawler.make_volume_epub(novel_info_path=novel_info_path)
    else:
        crawler.make_epub(novel_info_path=novel_info_path)


def novel_crawl():
    novel_url = input("Enter the novel URL: ").strip()
    output_dir = input("Enter the output directory (leave blank for default): ").strip()
    sleep_time = int(input("Enter sleep time between requests in milliseconds (default 1000): ").strip() or "1000")
    crawl_type = input("Enter crawl type (full/range/single, default full): ").strip() or "full"
    custom_volume_list = None
    custom_volume_list_ = input("Enter custom volume list (comma-separated URLs, leave blank for none, only for NovelRequest type): ").strip() or None
    if custom_volume_list_:
        custom_volume_list = [url.strip() for url in custom_volume_list_.split(",")]
    crawl_type_args = {"custom_volume_list": custom_volume_list} if custom_volume_list_ else {}
    novel_info_url = input("Enter novel info URL (leave blank for none): ").strip() or None if custom_volume_list_ else None
    if novel_info_url:
        crawl_type_args["info_url"] = novel_info_url
    if crawl_type == "range":
        start_chapter = int(input("Enter start chapter number: ").strip())
        end_chapter = int(input("Enter end chapter number: ").strip())
        crawl_type_args["start_chapter"] = start_chapter
        crawl_type_args["end_chapter"] = end_chapter
    elif crawl_type == "single":
        chapter_url = input("Enter the chapter URL: ").strip()
        crawl_type_args["chapter_url"] = chapter_url
    book_type = input("Enter book type (all/volume, default all): ").strip() or "all"
    keep_logged_in = input("Keep logged in during crawling? (y/n, default n): ").strip().lower() == "y"

    run(novel_url=novel_url, output_dir=output_dir or None, sleep_time=sleep_time,
        crawl_type=crawl_type, crawl_type_args=crawl_type_args, book_type=book_type, keep_logged_in=keep_logged_in)

    
