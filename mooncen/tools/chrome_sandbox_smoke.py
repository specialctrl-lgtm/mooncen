from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from selenium.webdriver.chrome.options import Options

from Crawler.selenium_driver import build_chrome_driver


MARKER = "mooncen-chrome-sandbox-ok"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="mooncen-chrome-smoke-") as profile:
        options = Options()
        for argument in (
            "--headless=new",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-sync",
            "--metrics-recording-only",
            "--mute-audio",
            "--no-default-browser-check",
            "--no-first-run",
            f"--user-data-dir={profile}",
        ):
            options.add_argument(argument)
        driver = build_chrome_driver(options)
        try:
            driver.get(f"data:text/html,<main id='result'>{MARKER}</main>")
            if driver.find_element("id", "result").text != MARKER:
                raise RuntimeError("Chrome sandbox smoke marker was not rendered")
        finally:
            driver.quit()
    print("chrome_sandbox_smoke=ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
