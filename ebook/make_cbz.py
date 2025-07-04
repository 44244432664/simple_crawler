import os
import re
import zipfile
from PIL import Image, ImageFile
import shutil

def create_cbz(comic_path, chapter, delete_page_0=False, delete_chapter_dir=True):
        """
        Create cbz from images
        """
        if not os.path.exists(f"{comic_path}/chapter {chapter}"):
            print(f"No images found for chapter {chapter}")
            return "error"
        img_ext = [".jpg", ".jpeg", ".png", ".gif"]
        fnames = [i for i in os.listdir(os.path.join(comic_path, f"chapter {chapter}")) if os.path.splitext(i)[1].lower() in img_ext]
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
            print(f"No images found in {comic_path}/chapter {chapter}")
            return "error"
        # sort filenames
        # print(f"Total: {len(fnames)} images found in chapter {chapter}")
        if not os.path.exists(comic_path+"/chapter "+str(chapter)):
            print("No img directory found")
            return "error"
        # create cbz from images
        try:
            with zipfile.ZipFile(f"{comic_path}/chapter {str(chapter)}.cbz", "w") as cbz:
                for img in fnames:
                    cbz.write(os.path.join(comic_path, f"chapter {chapter}", img), img)
            # print("Saved chapter "+ str(chapter))
        except Exception as e:
            print("Error creating cbz: ", e)
            return "error"
        
        # remove img directory after creating cbz
        if delete_chapter_dir:
            # print(f"Removing chapter {chapter} directory after creating cbz")
            shutil.rmtree(f"{comic_path}/chapter {str(chapter)}")
        # print(f"Removed chapter {chapter} directory after creating cbz")
        return "cbz created"