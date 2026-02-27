import os

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
def init_selenium_driver(headless: bool = False, driver_path: str | None = None) -> webdriver.Chrome:
    options = Options()
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    if headless:
        options.add_argument('--headless=new')

    executable = driver_path or os.getenv('CHROMEDRIVER_PATH')

    if executable:
        service = Service(executable)
    else:
        try:
            service = Service(ChromeDriverManager().install())
        except Exception as exc:
            raise RuntimeError(
                'Unable to download compatible ChromeDriver. '
                'Set CHROMEDRIVER_PATH to an existing driver binary.'
            ) from exc

    return webdriver.Chrome(service=service, options=options)
