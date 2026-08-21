from __future__ import annotations

import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service


DEFAULT_CHROME_BINARY = "/snap/chromium/current/usr/lib/chromium-browser/chrome"
DEFAULT_CHROMEDRIVER = "/snap/chromium/current/usr/lib/chromium-browser/chromedriver"


def build_chrome_driver(options: Options):
    chrome_binary = os.getenv("CHROME_BINARY") or DEFAULT_CHROME_BINARY
    chromedriver = os.getenv("CHROMEDRIVER") or DEFAULT_CHROMEDRIVER

    if chrome_binary and Path(chrome_binary).exists():
        options.binary_location = chrome_binary

    if chromedriver and Path(chromedriver).exists():
        return webdriver.Chrome(service=Service(chromedriver), options=options)

    return webdriver.Chrome(options=options)
