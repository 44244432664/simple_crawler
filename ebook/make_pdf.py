import os
import re
from PIL import Image, ImageFile
import shutil


def create_pdf(comic_path, chapter, delete_page_0=False, delete_chapter_dir=True):
        """
        Create pdf from images
        """
        if not os.path.exists(f"{comic_path}/chapter {chapter}"):
            print(f"No images found for chapter {chapter}")
            # print("No images found")
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
            fnames = fnames[1:]  # remove first page
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
        # create pdf from images
        try:
            imgs = [Image.open(os.path.join(comic_path+"/chapter "+str(chapter), i)) for i in fnames]
            # print("Total images: ", len(imgs))
            ImageFile.LOAD_TRUNCATED_IMAGES = True  # allow truncated images
            imgs[0].save(f"{comic_path}/chapter {str(chapter)}.pdf", "PDF", save_all=True, append_images=imgs[1:], resolution=100.0) # , quality=95)
            # print("Saved chapter "+ str(chapter))
            # os.rmdir(f"{self.path}/chapter {str(chapter)}")  # remove img directory after creating pdf

            # Remove directory and all its contents
            if delete_chapter_dir:
                # print(f"Removing chapter {chapter} directory after creating pdf")
                shutil.rmtree(f"{comic_path}/chapter {str(chapter)}")
            # print(f"Removed chapter {chapter} directory after creating pdf")
        except Exception as e:
            print("Error creating pdf: ", e)
            return "error"
        return "pdf created"
