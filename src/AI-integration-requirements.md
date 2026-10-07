Modularize the crawl task of the whole project into small, generic modules:
- `get_user_input`: get user's input.
- `get_metadata`: crawl cover, author, desciption, number of volumes, etc.
- [loop] `volumes_prepare`: get/crawl volumes name, cover (if they exist, use fallback if not), and chapter urls inside each volume.
- [loop] [progress bar] `crawl_chapter`: contain at least `download_image`, and/or `crawl_text_content`. Crawl all defined chapters (all chapter, in prepared volumes, specific chapter, etc.) in user's input.
- `download_image`: download all one chapter's images (no matter comic, novel, or gallery*), rename the downloaded as defined in the project. The images' information (image name after rename, full image tag. Example <img src=""> or similar, defined in `data/formats/<site>.json` files) will be stored under `<output_folfer, usually 'outputs'>/<name>/img/img_info.json.
- `crawl_text_content`: when crawling a novel with text, crawl chapter's text content (as in `Novel.py`) with only raw image tag with url (do not download the image. It is handled by `download_image` module).
- `create_ebook`: depend on user's input, create `pdf`, `epub`, `cbz`, or `folder` output file(s). Change the raw image url into the renamed image, using perfect matching raw image tag in novel and image tag stored in `img_info.json`. Finally, export the ebook.
- `build_flow`: using `flow` field defined in `data/formats/<site>.json` files to create the crawl flow using the modules listed above.
- `get_raw_page`: use request, or selenium get to get the url's HTML raw file (similar to `get_page`).
- `AI_update`: when encounter sites not in the current data base, use AI (Groq/OpenAI/Gemini/Claude/Ollama/etc.) API with modules to do the following:
    - use `get_page`/`get_raw_page` to get the user's input url/site (site contains meta data, volumes, chapter urls, etc.).
    - use AI to determine meta data tag as in `data/formats/<site>.json`, or `data/template` json files with volume section tag (if exist), chapter urls/section tag, get the url of first chapter in the chapter list, and determine the rule for naming chapter (in case of comics/novels with decimal chapters like 12.5, 1.1, etc.).
    - continue to use `get_page`/`get_raw_page` to get the raw HTML of the first chapter in previous step.
    - use AI to determine chapter's title tag, content tag, image tag (in case dev don't use <img>), etc. to complete the `format` field in format json file (json files and template files before this update).
    - use AI to create a 'crawl flow' in `flow` field of the format json file with modules above `build_flow` for the module to build the flow later.

Note:
- [loop]: loop with certain condition (like total number of volumes `volumes_prepare`, or total number of chapters in `crawl_chapter`)
- [progress bar]: put a progress bar here
- *: for gallery crawling, put a progress bar for image/total images
- DO NOT modify the existing json files `data/formats` or `data/template` during implementation (changes in these files will be made explicitly by user, or user's prompt with permission)
- create `.env` file for system prompt storing
- use `AI_API` field in `data/secret.json` for AI API use