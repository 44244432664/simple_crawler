from crawler.crawl_qq import QQCrawler, control_QQcrawler
from crawler.crawl_wikidich import WikiCrawler, control_Wikidich_crawler
from crawler.crawl_xx import crawl_xx, xx_control
from crawler.crawl_nh import crawl_nh, nh_control

from ebook.make_pdf import create_pdf
from ebook.make_cbz import create_cbz, files2cbz
from ebook.epub import create_epub
from ebook.cbz_converter import cbz2pdf
import os
import shutil


def main():
    continue_crawl = True
    while continue_crawl:
        print("1. Crawl QQ novel")
        print("2. Crawl WikiDich novel")
        # print("3. Create PDF from images")
        # print("4. Create CBZ from images")
        # print("5. Create EPUB from novel info")
        print("3. Convert CBZ to PDF")
        # print("4. Crawl XX novel")
        print("0. Exit")

        choice = input("Choose an option: ")

        if choice == "1":
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
            
        elif choice == "2":
            try:
                link = input("Enter the Wattpad/Wikidich novel link: ")
                output_dir = input("Enter the output directory (default is the novel title of the current directory): ")
                show_book_info_after_crawl = input("Show book info after crawling? (y/n): ").strip().lower() == "y"
                crawler = WikiCrawler(link, output_dir)

                action = input("Enter action (get_all, get_chapter_range, get_chapter): ")
                make_book_option = int(input("""How do you want to save the chapters?
1. Save as individual chapter books
2. Save as a single book with chapters stored in the novel_info.json file of output directory (you have to have the chapters content in the novel_info.json file)
3. Exit (Note that the chapters you crawled will still be saved in the novel_info.json file for later use)
Enter your choice (1/2/3): """))

                if action == "get_all":
                    args = []
                elif action == "get_chapter_range":
                    start_chapter = int(input("Enter start chapter: "))
                    end_chapter = int(input("Enter end chapter: "))
                    args = [start_chapter, end_chapter]
                elif action == "get_chapter":
                    chapter = input("Enter list of chapter numbers (e.g., [1,2,3]) or list of <chapter links>: ")
                    if len(chapter) == 0:
                        print("No chapter selected. Please try again.")
                        continue
                    args = [eval(chapter)]
                else:
                    print("Invalid action. Please try again.")
                    continue

                control_Wikidich_crawler(crawler, action, show_book_info_after_crawl, make_book_option, *args)
            except Exception as e:
                raise Exception("Error during WikiDich crawling: " + str(e))
            
        elif choice == "3":
            try:
                input_dir = input("Enter the input directory (containing CBZ files): ")
                output_dir = input("Enter the output directory for PDF files (default is the input directory): ") or None
                convert_option = input("Convert all CBZ files to PDF? (y/n): ").strip().lower() == "y"
                if convert_option:
                    for filename in os.listdir(input_dir):
                        if filename.endswith(".cbz"):
                            cbz_path = os.path.join(input_dir, filename)
                            output_pdf_path = os.path.join(output_dir, os.path.splitext(filename)[0] + '.pdf') if output_dir else None
                            cbz2pdf(cbz_path, output_pdf_path)
                else:
                    chapters = input("Enter list of chapter numbers (e.g., [1,2,3]) or list of <chapter names>: ")
                    chapters = eval(chapters)
                    for chapter in chapters:
                        if isinstance(chapter, int):
                            cbz_path = os.path.join(input_dir, f"chapter {chapter}.cbz")
                            output_pdf_path = os.path.join(output_dir, f"chapter {chapter}.pdf") if output_dir else None
                            cbz2pdf(cbz_path, output_pdf_path)
                        elif isinstance(chapter, str):
                            cbz_path = os.path.join(input_dir, f"{chapter}.cbz")
                            output_pdf_path = os.path.join(output_dir, f"{chapter}.pdf") if output_dir else None
                            cbz2pdf(cbz_path, output_pdf_path)
                        else:
                            print(f"Invalid chapter format: {chapter}")
            except Exception as e:
                raise Exception("Error during CBZ to PDF conversion: " + str(e))
        elif choice == "xx_mode":
            try:
                xx_control()
            except Exception as e:
                raise Exception("Error during XX novel crawling: " + str(e))
        elif choice == "nh_mode":
            try:
                nh_control()
            except Exception as e:
                raise Exception("Error during nhentai crawling: " + str(e))
        elif choice == "0":
            print("Exiting the program.")
            return
        else:
            print("Invalid choice. Please try again.")
            continue
        continue_crawl = input("Do you want to continue? (y/n): ").strip().lower() == "y"
    return

        
if __name__ == "__main__":
    main()
    # Uncomment the following lines to test the create_pdf and create_cbz functions
    # create_pdf("path/to/comic", 1, delete_page_0=True, delete_chapter_dir=True)
    # create_cbz("path/to/comic", 1, delete_page_0=True, delete_chapter_dir=True)
    # cbz2pdf("path/to/comic/chapter 1.cbz", "path/to/output/chapter 1.pdf")
    # create_epub("path/to/novel_info.json", "path/to/output")