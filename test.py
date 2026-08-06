try:
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options
except ImportError:
    webdriver = None
    Options = None
from bs4 import BeautifulSoup
import time
import os
import pickle

import requests
import json


def get_html(url):
    respose = requests.get(url)
    return respose.text


def get_login_token(url, session=None):
    # Get the login page to retrieve the token and cookies
    session = session or requests.Session()
    response = session.get(url)
    if response.status_code != 200:
        # print("Failed to load login page. Status code: " + str(response.status_code))
        return False

    soup = BeautifulSoup(response.text, 'html.parser')
    try:
        token = soup.find('input', {'name': '_token'})['value']
    except Exception as e:
        # print("Failed to retrieve token: " + str(e))
        token = ""

    # print((token, session.cookies.get_dict()))

    return token
# , session.cookies.get_dict()


def login(**args):
    # start = time.time()
    url = args.get('url')
    username = args.get('username')
    password = args.get('password')
    token = args.get('token')
    session = args.get('session')


    login_data = {
        # 'name': username,
        "username": username,
        'password': password
        # 'remember': 'on'
    }
    if len(token) > 0:
        # print("Token retrieved: " + token)
        # print("Cookies retrieved: " + str(session.cookies.get_dict()))
        login_data['_token'] = token
    else:
        token = ""

    requester = session or requests
    login_response = requester.post(url, data=login_data)
    if login_response.status_code == 200:
        # print("Login successful")
        # print("Login response cookies: " + str(login_response.cookies.get_dict()))
        # end = time.time()
        # print(f"Login took {end - start} seconds")
        # print("Final cookies after login: " + str(login_response.cookies.get_dict()))
        return session.cookies.get_dict() if session else login_response.cookies.get_dict()
    else:
        # print("Login failed. Status code: " + str(login_response.status_code))
        return False


def get_page_with_cookies(url, cookies, session=None, save_path=None):
    requester = session or requests
    if session:
        session.cookies.update(cookies)
    response = requester.get(url, cookies=cookies)
    if response.status_code == 200:
        if save_path:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(BeautifulSoup(response.text, 'html.parser').prettify())
        return response.text
    else:
        # print("Failed to load page. Status code: " + str(response.status_code))
        return False


def get_page_with_cookies1(url, cookies):
    driver = webdriver.Chrome()

    print("cookies to add:")
    print(cookies)

    driver.get("https://valvrareteam.net")  # Load the base domain to set cookies

    for name, value in cookies.items():
        print(f"Adding cookie: {name}={value}")
        driver.add_cookie({'name': name, 'value': value})

    # driver.get(url)

    driver.get(url)
    time.sleep(5)  # Wait for the page to load
    page_source = driver.page_source
    driver.quit()
    if page_source:
        with open("test_page.html", "w", encoding="utf-8") as f:
            f.write(BeautifulSoup(page_source, 'html.parser').prettify())
        # print("Page loaded successfully with selenium cookies.")
        return page_source
    else:
        # print("Failed to load page.")
        return False

if __name__ == "__main__":
    # chapter_urls = [
    #     "https://www.foxaholic.com/novel/i-got-a-new-skill-every-time-i-was-exiled-and-after-100-different-worlds-i-was-unmatched/chapter-1/",
    #     "https://www.foxaholic.com/novel/i-got-a-new-skill-every-time-i-was-exiled-and-after-100-different-worlds-i-was-unmatched/chapter-2/",
    #     # Add more chapter URLs as needed
    # ]

    # novel_name = "Test_Novel"
    # # scrape_novel(chapter_urls, novel_name)
    # scrape_with_uc(chapter_urls, novel_name)
    login1 = "'https://docln.sbs/login'"
    url1 = "https://docln.sbs/truyen/19959-dare-ga-yuusha-wo-koroshita-ka"
    login_ = "https://valvrareteam.net/api/auth/login"
    url = "https://valvrareteam.net/truyen/no-game-no-life-cd23c8d9"

    try:
        with open("data/secrets.json", "r", encoding="utf-8") as secrets_file:
            credentials = json.load(secrets_file).get("valvrareteam", {})
        username = credentials["username"]
        password = credentials["password"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(
            "Configure valvrareteam username and password in ignored data/secrets.json"
        ) from error

    login_cookies = login(url=login_, username=username, password=password, token="")
    if login_cookies:
        print("Login successful. Cookies after login: " + str(login_cookies))
        page_content = get_page_with_cookies1(url, login_cookies)
        if page_content:
            print("Page loaded successfully with selenium cookies.")
        else:
            print("Failed to load page with selenium cookies.")
