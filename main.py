from crawler.crawl_qq import QQCrawler, control_QQcrawler
# from crawler.crawl_wikidich import WikiCrawler, control_Wikidich_crawler
from crawler.crawl_xx import crawl_xx, xx_control
# from crawler.crawl_nh import crawl_nh, nh_control
# from crawler.crawl_docln import *
# from crawler.Novel import *
from crawler.Novel import *

# from utils.novel import *

from ebook.make_pdf import create_pdf
from ebook.make_cbz import create_cbz, files2cbz
from ebook.epub import create_epub
from ebook.cbz_converter import cbz2pdf
import os
import shutil


def main():
    continue_crawl = True
    while continue_crawl:
#         print("1. Crawl QQ novel")
#         print("2. Crawl WikiDich novel")
#         # print("3. Create PDF from images")
#         # print("4. Create CBZ from images")
#         # print("5. Create EPUB from novel info")
#         print("3. Crawl Docln novel")
#         print("4. Convert CBZ to PDF (not developed yet :>>>)")
#         # print("4. Crawl XX novel")
#         print("0. Exit")

#         choice = input("Choose an option: ")

#         if choice == "1":
#             try:
#                 url = input("Enter QQ novel URL: ")
#                 crawler = QQCrawler(url)
#                 # crawler.crawl()
#                 action = input("Enter action (get_all, get_chapter_range, get_chapter): ")
                
#                 delete_page_0 = input("Delete first page? (y/n): ").strip().lower() == "y"
#                 if delete_page_0:
#                     print("First page WILL be deleted from pdf")
#                 else:
#                     print("First page will NOT be deleted from pdf")

#                 delete_chapter_dir = input("Delete chapter directory after creating pdf/cbz? (y/n): ").strip().lower() == "y"
#                 if delete_chapter_dir:
#                     print("Chapter directory WILL be deleted after creating pdf/cbz")
#                 else:
#                     print("Chapter directory will NOT be deleted after creating pdf/cbz")

#                 create_cbz = input("Create cbz instead of pdf? (y/n) (recommend cbz for manga, pdf for non-manga): ").strip().lower() == "y"

#                 if action == "get_all":
#                     args = [delete_page_0, create_cbz, delete_chapter_dir]
#                 elif action == "get_chapter_range":
#                     start_chapter = int(input("Enter start chapter: "))
#                     end_chapter = int(input("Enter end chapter: "))
#                     args = [start_chapter, end_chapter, delete_page_0, create_cbz, delete_chapter_dir]
#                 elif action == "get_chapter":
#                     chapter = int(input("Enter chapter number: "))
#                     args = [chapter, delete_page_0, create_cbz, delete_chapter_dir]
#                 else:
#                     print("Invalid action. Please try again.")
#                     continue

#                 control_QQcrawler(crawler, action, *args)
#             except Exception as e:
#                 raise Exception("Error during QQ comic crawling: " + str(e))
            
#         elif choice == "2":
#             try:
#                 link = input("Enter the Wattpad/Wikidich novel link: ")
#                 output_dir = input("Enter the output directory (default is the novel title of the current directory): ")
#                 show_book_info_after_crawl = input("Show book info after crawling? (y/n): ").strip().lower() == "y"
#                 crawler = WikiCrawler(link, output_dir)

#                 action = input("Enter action (get_all, get_chapter_range, get_chapter): ")
#                 make_book_option = int(input("""How do you want to save the chapters?
# 1. Save as individual chapter books
# 2. Save as a single book with chapters stored in the novel_info.json file of output directory (you have to have the chapters content in the novel_info.json file)
# 3. Exit (Note that the chapters you crawled will still be saved in the novel_info.json file for later use)
# Enter your choice (1/2/3): """))

#                 if action == "get_all":
#                     args = []
#                 elif action == "get_chapter_range":
#                     start_chapter = int(input("Enter start chapter: "))
#                     end_chapter = int(input("Enter end chapter: "))
#                     args = [start_chapter, end_chapter]
#                 elif action == "get_chapter":
#                     chapter = input("Enter list of chapter numbers (e.g., [1,2,3]) or list of <chapter links>: ")
#                     if len(chapter) == 0:
#                         print("No chapter selected. Please try again.")
#                         continue
#                     args = [eval(chapter)]
#                 else:
#                     print("Invalid action. Please try again.")
#                     continue

#                 control_Wikidich_crawler(crawler, action, show_book_info_after_crawl, make_book_option, *args)
#             except Exception as e:
#                 raise Exception("Error during WikiDich crawling: " + str(e))
            
#         elif choice == "3":
#             print("\n\nDocln crawling selected.")
#             print("""\nNotes:
# 1. The novel info link is used to get the novel metadata and chapter list in case you want to crawl entire novel according to novel link.
# \n2. list of custom volume links is used when you want to crawl specific volumes and they may lies in different novel links (different trans team, different projects etc.).
# \n3. Yes, you can use 2 to make an abomination novel with volumes from different novels :>>>. But for the sake of my sanity, please make sure all chapters are from the same novel.
# \n4. For chapter range crawling, custom volume list is optional, and range is within the specified volumes flattened.
# \n5. For single chapter crawling, custom volume list is not used, just provide the chapter link and info link.
# \n6. compile_type default is single_volume, single chapter crawling compile_type is single_volume (obviously :>).
#                   """)
#             action = input("""\nwhat action do you want to perform:
# 1. Crawl entire novel or custom volumes
# 2. Crawl chapter range
# 3. Crawl single chapter
# Enter your choice (1/2/3): """)
#             args = []
#             if action == "1":
#                 novel_link = input("Enter the Docln novel info link: : ")
#                 custom_volume_list = input("Please provide a list of custom volume links: ")
#                 if custom_volume_list.strip() == "":
#                     custom_volume_list = None
#                 else:
#                     custom_volume_list = eval(custom_volume_list)

#                 args = [custom_volume_list, novel_link]

#             elif action == "2":
#                 novel_link = input("Enter the Docln novel info link: ")
#                 start_chapter = int(input("Enter start chapter number: "))
#                 end_chapter = int(input("Enter end chapter number: "))
#                 custom_volume_list = input("Please provide a list of custom volume links: ")
#                 if custom_volume_list.strip() == "":
#                     custom_volume_list = None
#                 else:
#                     custom_volume_list = eval(custom_volume_list)

#                 args = [start_chapter, end_chapter, custom_volume_list, novel_link]
                

#             elif action == "3":
#                 novel_link = input("Enter the Docln novel info link: ")
#                 chapter_link = input("Enter the Docln chapter link: ")

#                 args = [chapter_link, novel_link]

#             elif action == "test":
#                 print("Entered my mode of hard coded crawl :>>>")
#                 test()
#                 break
                

#             output_dir = input("Enter the output directory (default is the outputs/Novel/<novel title> of the current directory): ")

#             sleep_time = input("Enter sleep time between crawl requests in seconds (default is 1000ms): ")
#             if sleep_time.strip() == "":
#                 sleep_time = 1000
#             else:
#                 sleep_time = int(sleep_time)
            
#             if output_dir.strip() == "":
#                 output_dir = None

#             compile_type = "single_volume"
#             if action in ["1", "2"]:
#                 compile_type = input("Enter compile type (single_volume, multi_volume): ")
#                 if compile_type.strip() == "":
#                     compile_type = "single_volume"
#                 elif compile_type not in ["single_volume", "multi_volume"]:
#                     print("Invalid compile type. Defaulting to single_volume.")
#                     compile_type = "single_volume"


#             action_map = {
#                 "1": "get_all",
#                 "2": "get_chapter_range",
#                 "3": "get_chapter",
#             }
#             try:
#                 crawler = DoclnCrawler(novel_link, output_dir=output_dir, sleep_time=sleep_time)
#                 docln_crawler_control(crawler, action_map[action], *args, compile_type=compile_type)
#             except Exception as e:
#                 raise Exception("Error during Docln novel crawling: " + str(e))


#         elif choice == "4":
#             try:
#                 input_dir = input("Enter the input directory (containing CBZ files): ")
#                 output_dir = input("Enter the output directory for PDF files (default is the input directory): ") or None
#                 convert_option = input("Convert all CBZ files to PDF? (y/n): ").strip().lower() == "y"
#                 if convert_option:
#                     for filename in os.listdir(input_dir):
#                         if filename.endswith(".cbz"):
#                             cbz_path = os.path.join(input_dir, filename)
#                             output_pdf_path = os.path.join(output_dir, os.path.splitext(filename)[0] + '.pdf') if output_dir else None
#                             cbz2pdf(cbz_path, output_pdf_path)
#                 else:
#                     chapters = input("Enter list of chapter numbers (e.g., [1,2,3]) or list of <chapter names>: ")
#                     chapters = eval(chapters)
#                     for chapter in chapters:
#                         if isinstance(chapter, int):
#                             cbz_path = os.path.join(input_dir, f"chapter {chapter}.cbz")
#                             output_pdf_path = os.path.join(output_dir, f"chapter {chapter}.pdf") if output_dir else None
#                             cbz2pdf(cbz_path, output_pdf_path)
#                         elif isinstance(chapter, str):
#                             cbz_path = os.path.join(input_dir, f"{chapter}.cbz")
#                             output_pdf_path = os.path.join(output_dir, f"{chapter}.pdf") if output_dir else None
#                             cbz2pdf(cbz_path, output_pdf_path)
#                         else:
#                             print(f"Invalid chapter format: {chapter}")
#             except Exception as e:
#                 raise Exception("Error during CBZ to PDF conversion: " + str(e))
        # elif choice == "xx_mode":
        #     try:
        #         xx_control()
        #     except Exception as e:
        #         raise Exception("Error during XX novel crawling: " + str(e))
#         elif choice == "nh_mode":
#             try:
#                 nh_control()
#             except Exception as e:
#                 raise Exception("Error during nhentai crawling: " + str(e))
#         elif choice == "0":
#             print("Exiting the program.")
#             return
#         else:
#             print("Invalid choice. Please try again.")
#             continue
#         continue_crawl = input("Do you want to continue? (y/n): ").strip().lower() == "y"
#     return

        crawl_choice = input("Enter 'comic' to crawl comic, 'novel' to crawl novel, " \
        "'epub' to create epub from novel info path, " \
        "'multi' to crawl multiple novels from a JSON 'to_crawl.json' file, " \
        "db to get novel source database, or 'exit' to quit: ").strip().lower()
        if crawl_choice == "comic":
            qq = input("Are you crawling a QQ comic? (y/n): ").strip().lower() == "y"
            if not qq:
                comic_url = input("Enter the comic URL: ")
                save_path = input("Enter the save path for the comic (default is current directory): ").strip() or "."
                img_ext = input("Enter the image extension to save (default is jpg): ").strip() or "jpg"
                make_cbz_option = input("Do you want to create CBZ files? (y/n): ").strip().lower() == "y"
                make_pdf_option = input("Do you want to create PDF files? (y/n): ").strip().lower() == "y"
                selenium_driver_option = input("Do you want to use Selenium driver for crawling? (y/n): ").strip().lower() == "y"
            else:
                try:
                        url = input("Enter QQ novel URL: ")
                        crawler = QQCrawler(url)
                        # crawler.crawl()
                        action = input("Enter action (get_all, get_chapter_range, get_chapter): ")
                        
                        delete_page_0 = input("Delete first page? (y/n): ").strip().lower() == "y"
                        if delete_page_0:
                                print("First page WILL be deleted from pdf")
                        else:
                                print("First page will NOT be deleted from pdf")

                        delete_chapter_dir = input("Delete chapter directory after creating pdf/cbz? (y/n): ").strip().lower() == "y"
                        if delete_chapter_dir:
                                print("Chapter directory WILL be deleted after creating pdf/cbz")
                        else:
                                print("Chapter directory will NOT be deleted after creating pdf/cbz")

                        create_cbz = input("Create cbz instead of pdf? (y/n) (recommend cbz for manga, pdf for non-manga): ").strip().lower() == "y"

                        if action == "get_all":
                                args = [delete_page_0, create_cbz, delete_chapter_dir]
                        elif action == "get_chapter_range":
                                start_chapter = int(input("Enter start chapter: "))
                                end_chapter = int(input("Enter end chapter: "))
                                args = [start_chapter, end_chapter, delete_page_0, create_cbz, delete_chapter_dir]
                        elif action == "get_chapter":
                                chapter = int(input("Enter chapter number: "))
                                args = [chapter, delete_page_0, create_cbz, delete_chapter_dir]
                        else:
                                print("Invalid action. Please try again.")
                                continue

                        control_QQcrawler(crawler, action, *args)
                except Exception as e:
                        raise Exception("Error during QQ comic crawling: " + str(e))

        #     run(comic_url, save_path, img_ext, make_cbz_option, make_pdf_option, selenium_driver_option)
        elif crawl_choice == "novel":
                novel_crawl()
        elif crawl_choice == "epub":
                make_epub()
        elif crawl_choice == "multi":
                json_path = input("Enter the path to the 'to_crawl.json' file: ").strip()
                if not json_path:
                        print("No path provided. Please try again.")
                        continue
                crawl_multi(json_path)
        elif crawl_choice == "db":
                db = get_data_base()
                print(db)
        elif crawl_choice == "exit":
                print("Exiting the program.")
                return
        else:
                print("Invalid choice. Please try again.")
                continue
        continue_crawl = input("Do you want to continue? (y/n): ").strip().lower() == "y"
    return

    
        
if __name__ == "__main__":
    main()
#     print("testing novel crawler...")
    # test()
#     novel_crawl()

    
    # Uncomment the following lines to test the create_pdf and create_cbz functions
    # create_pdf("path/to/comic", 1, delete_page_0=True, delete_chapter_dir=True)
    # create_cbz("path/to/comic", 1, delete_page_0=True, delete_chapter_dir=True)
    # cbz2pdf("path/to/comic/chapter 1.cbz", "path/to/output/chapter 1.pdf")
    # create_epub("path/to/novel_info.json", "path/to/output")