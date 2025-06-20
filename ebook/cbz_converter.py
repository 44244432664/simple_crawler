import zipfile
import os
from PIL import Image
from PIL import ImageFile
import tempfile

def cbz2pdf(cbz_path, output_pdf_path=None):
    """
    Convert a CBZ file to a PDF file.
    
    :param cbz_path: Path to the input CBZ file.
    :param output_pdf_path: Path where the output PDF will be saved.
    """
    # Ensure the input file exists
    if not os.path.isfile(cbz_path):
        raise FileNotFoundError(f"CBZ file not found: {cbz_path}")

    if output_pdf_path is None:
        # Generate output PDF path based on input CBZ path
        output_pdf_path = os.path.splitext(cbz_path)[0] + '.pdf'

    # Create a temporary directory to extract images
    with tempfile.TemporaryDirectory() as temp_dir:
        with zipfile.ZipFile(cbz_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)

        # Collect image files from the temporary directory
        image_files = [os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))]
        image_files.sort()  # Sort files to maintain order

        # Create a PDF from the images
        images = []
        for image_file in image_files:
            img = Image.open(image_file)
            # img = img.convert('RGB')  # Convert to RGB if not already
            images.append(img)

        if images:
            ImageFile.LOAD_TRUNCATED_IMAGES = True  # allow truncated images
            images[0].save(output_pdf_path, save_all=True, append_images=images[1:], resolution=100.0)
        else:
            raise ValueError("No valid image files found in the CBZ archive.")
        
    print(f"PDF created successfully: {output_pdf_path}")