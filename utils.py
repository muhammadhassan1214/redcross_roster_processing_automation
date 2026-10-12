import os
import re
import sys
import json
import time
import logging
import tempfile
import urllib.parse
from datetime import datetime, date
from typing import Optional
from selenium import webdriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.select import Select
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, WebDriverException,
    ElementNotInteractableException, StaleElementReferenceException,
    NoAlertPresentException
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
download_dir = os.path.join(BASE_DIR, "downloads")
os.makedirs(download_dir, exist_ok=True)

STATE_FILE = os.path.join(BASE_DIR, "class_state.json")
LOG_FILE = os.path.join(BASE_DIR, "automation.log")

# Configure logging (console + file, so unattended runs leave a trail)
logger = logging.getLogger("automation")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    _console = logging.StreamHandler()
    _console.setFormatter(_fmt)
    logger.addHandler(_console)
    try:
        _file = logging.FileHandler(LOG_FILE, encoding="utf-8")
        _file.setFormatter(_fmt)
        logger.addHandler(_file)
    except OSError:
        pass
    logger.propagate = False


def safe_execute_with_retry(func, max_retries: int = 3, delay: float = 1.0, *args, **kwargs):
    """Execute a function with retry logic.

    Retries when the function raises OR returns False (the helper functions
    below swallow exceptions and return False, so previously retries never ran).
    """
    max_retries = max(1, max_retries)
    result = False
    for attempt in range(max_retries):
        try:
            result = func(*args, **kwargs)
            if result is False and attempt < max_retries - 1:
                logger.warning(f"Retrying {func.__name__} (attempt {attempt + 2}/{max_retries})")
                time.sleep(delay)
                continue
            return result
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Retrying {func.__name__} (attempt {attempt + 2}/{max_retries}): {e}")
                time.sleep(delay)
                continue
            logger.error(f"Failed {func.__name__} after {max_retries} attempts: {e}")
            raise
    return result


def click_element_by_js(driver, by_locator, timeout: int = 10, max_retries: int = 3) -> bool:
    """Click element using JavaScript with exception handling and retry logic."""
    def _js_click():
        try:
            element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(by_locator))
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center', inline: 'nearest'})", element)
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", element)
            time.sleep(0.5)
            return True
        except TimeoutException:
            logger.error(f"Element not found for JS click within {timeout} seconds: {by_locator}")
            return False
        except StaleElementReferenceException:
            return False
        except WebDriverException as e:
            logger.error(f"JavaScript execution failed: {e}")
            return False

    try:
        return bool(safe_execute_with_retry(_js_click, max_retries))
    except Exception as e:
        logger.error(f"Critical error in click_element_by_js: {e}")
        return False


def click_element(driver, by_locator, timeout: int = 10, max_retries: int = 3) -> bool:
    """Click element with exception handling and retry logic."""
    def _click():
        try:
            element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(by_locator))
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center', inline: 'nearest'})", element)
            time.sleep(0.5)
            try:
                element.click()
            except (ElementNotInteractableException, WebDriverException) as e:
                # Overlays / "element click intercepted" -> fall back to a JS click
                if "intercepted" in str(e).lower() or isinstance(e, ElementNotInteractableException):
                    driver.execute_script("arguments[0].click();", element)
                else:
                    raise
            time.sleep(0.5)
            return True
        except TimeoutException:
            logger.error(f"Element not found for click within {timeout} seconds: {by_locator}")
            return False
        except StaleElementReferenceException as e:
            logger.warning(f"Click failed due to element state: {e}")
            return False
        except WebDriverException as e:
            logger.error(f"WebDriver error during click: {e}")
            return False

    try:
        return bool(safe_execute_with_retry(_click, max_retries))
    except Exception as e:
        logger.error(f"Critical error in click_element: {e}")
        return False


def _clear_field(element):
    element.clear()
    time.sleep(0.2)
    # if not cleared, select-all + backspace (CONTROL on Windows/Linux, COMMAND on macOS)
    modifier = Keys.COMMAND if sys.platform == "darwin" else Keys.CONTROL
    element.send_keys(modifier + "a")
    element.send_keys(Keys.BACKSPACE)
    time.sleep(0.2)


def input_element(driver, by_locator, text, timeout: int = 10, max_retries: int = 3) -> bool:
    """Input text with exception handling and retries."""
    text = "" if text is None else str(text)

    def _input_text():
        try:
            element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(by_locator))
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center', inline: 'nearest'})", element)
            time.sleep(0.2)
            _clear_field(element)
            element.send_keys(text)
            time.sleep(0.3)
            return True
        except TimeoutException:
            logger.error(f"Input element not found within {timeout} seconds: {by_locator}")
            return False
        except StaleElementReferenceException:
            return False
        except (NoSuchElementException, ElementNotInteractableException) as e:
            logger.error(f"Element input failed: {e}")
            return False
        except WebDriverException as e:
            logger.error(f"WebDriver error during input: {e}")
            return False

    try:
        if not text:
            # Callers decide whether an empty value is acceptable; report it as a failure.
            logger.warning(f"Empty text provided for input: {by_locator}")
            return False
        return bool(safe_execute_with_retry(_input_text, max_retries))
    except Exception as e:
        logger.error(f"Critical error in input_element: {e}")
        return False


def input_element_by_typing(driver, by_locator, text, timeout: int = 10, max_retries: int = 3) -> bool:
    """Input text by simulating typing (for autocomplete fields)."""
    text = "" if text is None else str(text)

    def _type_text():
        try:
            element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(by_locator))
            driver.execute_script(
                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center', inline: 'nearest'})", element)
            time.sleep(0.2)
            _clear_field(element)
            for char in text:
                element.send_keys(char)
                time.sleep(0.05)
            time.sleep(0.3)
            return True
        except TimeoutException:
            logger.error(f"Input element not found within {timeout} seconds: {by_locator}")
            return False
        except StaleElementReferenceException:
            return False
        except (NoSuchElementException, ElementNotInteractableException) as e:
            logger.error(f"Element input failed: {e}")
            return False
        except WebDriverException as e:
            logger.error(f"WebDriver error during input: {e}")
            return False

    try:
        if not text:
            logger.warning(f"Empty text provided for input: {by_locator}")
            return False
        return bool(safe_execute_with_retry(_type_text, max_retries))
    except Exception as e:
        logger.error(f"Critical error in input_element_by_typing: {e}")
        return False


def get_element_text(driver, by_locator, timeout: int = 20, default: str = "") -> str:
    """Get element text with exception handling and default value."""
    try:
        element = WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(by_locator))
        text = element.text.strip()
        return text if text else default
    except TimeoutException:
        logger.warning(f"Element not visible for text extraction within {timeout} seconds: {by_locator}")
        return default
    except Exception as e:
        logger.error(f"Error getting element text: {e}")
        return default


def checkbox_is_checked(driver, by_locator, timeout: int = 10) -> bool:
    """Check if checkbox is checked with exception handling."""
    try:
        element = WebDriverWait(driver, timeout).until(EC.presence_of_element_located(by_locator))
        return element.is_selected()
    except TimeoutException:
        logger.warning(f"Checkbox not found within {timeout} seconds: {by_locator}")
        return False
    except Exception as e:
        logger.error(f"Error checking checkbox state: {e}")
        return False


def get_undetected_driver(headless: bool = False, max_retries: int = 3) -> Optional[webdriver.Chrome]:
    """Create Chrome driver with error handling (Selenium Manager resolves chromedriver)."""
    for attempt in range(max_retries):
        driver = None
        try:
            options = webdriver.ChromeOptions()
            path = os.path.join(BASE_DIR, "chrome-dir")
            try:
                os.makedirs(path, exist_ok=True)
            except OSError as e:
                logger.error(f"Failed to create chrome directory: {e}")
                path = None

            if path:
                options.add_argument(f"--user-data-dir={path}")

            prefs = {
                "download.default_directory": download_dir,
                "download.prompt_for_download": False,
                "directory_upgrade": True,
                "safebrowsing.enabled": True,
            }
            options.add_experimental_option("prefs", prefs)

            options.add_argument("--log-level=3")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-plugins")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-background-timer-throttling")
            options.add_argument("--disable-backgrounding-occluded-windows")
            options.add_argument("--disable-renderer-backgrounding")
            options.add_argument("--disable-features=TranslateUI")
            options.add_argument("--disable-ipc-flooding-protection")

            if headless:
                options.add_argument("--headless=new")
                options.add_argument("--disable-gpu")
                options.add_argument("--window-size=1920,1080")
            else:
                options.add_argument("--start-maximized")

            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)

            # An unexpected JS alert/confirm (e.g. "Are you sure?") would otherwise make
            # every later Selenium call raise UnexpectedAlertPresentException.
            options.set_capability("unhandledPromptBehavior", "accept")

            driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(90)
            time.sleep(2)

            # Apply stealth tweaks to every page that loads (not just the blank start page)
            try:
                driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": """
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                """})
            except Exception:
                pass

            driver.get("data:,")
            logger.info(f"Chrome driver initialized successfully (attempt {attempt + 1})")
            return driver

        except Exception as e:
            logger.error(f"Driver creation attempt {attempt + 1} failed: {e}")
            if "user data directory is already in use" in str(e).lower():
                logger.error("Another Chrome window is using the 'chrome-dir' profile. Close it (or kill leftover chrome.exe) and rerun.")
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            if attempt < max_retries - 1:
                time.sleep(3)

    logger.error("Max retries exceeded. Could not create the driver.")
    return None


def is_driver_alive(driver) -> bool:
    """True if the browser session is still usable."""
    if driver is None:
        return False
    try:
        _ = driver.window_handles
        return True
    except Exception:
        return False


def accept_alert_if_present(driver, timeout: int = 2) -> bool:
    try:
        WebDriverWait(driver, timeout).until(EC.alert_is_present())
        driver.switch_to.alert.accept()
        return True
    except (TimeoutException, NoAlertPresentException):
        return False
    except Exception:
        return False


def close_extra_tabs(driver, keep_handle) -> None:
    """Close every tab except keep_handle and switch back to it."""
    try:
        for handle in list(driver.window_handles):
            if handle != keep_handle:
                try:
                    driver.switch_to.window(handle)
                    driver.close()
                except Exception:
                    pass
        driver.switch_to.window(keep_handle)
    except Exception as e:
        logger.warning(f"Could not clean up tabs: {e}")


def check_element_exists(driver, by_locator, timeout: int = 3) -> bool:
    """Check if element is visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(by_locator))
        return True
    except Exception:
        return False


def wait_for_element_to_disappear(driver, by_locator, timeout: int = 30) -> bool:
    try:
        WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located(by_locator))
        return True
    except Exception:
        return False


def select_by_text(driver, by_locator, text: str, timeout: int = 10) -> bool:
    """Select dropdown option by visible text."""
    try:
        select_element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(by_locator))
        select = Select(select_element)
        options = [option.text.strip() for option in select.options]
        if text not in options:
            logger.error(f"Option '{text}' not found. Available options: {options}")
            return False
        select.select_by_visible_text(text)
        time.sleep(0.5)
        selected_option = select.first_selected_option.text.strip()
        if selected_option != text:
            logger.warning(f"Selection verification failed. Expected: '{text}', Selected: '{selected_option}'")
            return False
        return True
    except TimeoutException:
        logger.error(f"Select element not found within {timeout} seconds: {by_locator}")
        return False
    except Exception as e:
        logger.error(f"Select operation failed: {e}")
        return False


def select_by_value(driver, by_locator, value: str, timeout: int = 10) -> bool:
    """Select dropdown option by value."""
    try:
        select_element = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(by_locator))
        select = Select(select_element)
        values = [option.get_attribute("value") for option in select.options]
        if value not in values:
            logger.error(f"Option with value '{value}' not found. Available values: {values}")
            return False
        select.select_by_value(value)
        time.sleep(0.5)
        selected_option_value = select.first_selected_option.get_attribute("value")
        if selected_option_value != value:
            logger.warning(f"Selection verification failed. Expected: '{value}', Selected: '{selected_option_value}'")
            return False
        return True
    except TimeoutException:
        logger.error(f"Select element not found within {timeout} seconds: {by_locator}")
        return False
    except Exception as e:
        logger.error(f"Select operation failed: {e}")
        return False


def wait_for_page_load(driver, timeout: int = 30) -> bool:
    """Wait for page to fully load."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(1)
        return True
    except TimeoutException:
        logger.warning(f"Page load timeout after {timeout} seconds")
        return False
    except WebDriverException as e:
        logger.error(f"Error waiting for page load: {e}")
        return False


def safe_navigate_to_url(driver, url: str, max_retries: int = 3) -> bool:
    """Navigate to URL with retry logic."""
    for attempt in range(max_retries):
        try:
            driver.get(url)
            if wait_for_page_load(driver):
                logger.info(f"Successfully navigated to: {url}")
                return True
            logger.warning(f"Page load incomplete for: {url}")
        except TimeoutException:
            logger.warning(f"Navigation attempt {attempt + 1} timed out: {url}")
        except WebDriverException as e:
            logger.error(f"Navigation attempt {attempt + 1} failed: {e}")
        if attempt < max_retries - 1:
            time.sleep(2)

    logger.error(f"Failed to navigate to {url} after {max_retries} attempts")
    return False


def wait_while_element_is_displaying(driver, by, identifier, timeout=10):
    """Waits while the specified element is displayed."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            element = driver.find_element(by, identifier)
            if not element.is_displayed():
                return
        except Exception:
            return
        time.sleep(0.5)
    logger.warning(f"Timeout waiting for element {identifier} to stop displaying.")


def _normalize_time(t: str) -> str:
    """'9AM' / '9 am' / '9:00am' -> '9:00 AM'."""
    m = re.match(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?\s*[Mm]\.?\s*$", t)
    if not m:
        return t.strip()
    hour, minute, ampm = m.groups()
    return f"{int(hour)}:{minute or '00'} {ampm.upper()}M"


def parse_event_string(text: str) -> dict:
    """Parse 'Tuesday, October 6, 2026 · 9:00 AM–1:00 PM' (tolerant of spacing/dash variants)."""
    cleaned = (text or "").replace("\xa0", " ").replace(" ", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    time_re = r"\d{1,2}(?::\d{2})?\s*[AaPp]\.?\s*[Mm]\.?"
    pattern = (
        r"^(?:[A-Za-z]+,?\s+)?"                      # optional weekday
        r"([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})"        # date
        r"\s*[·•|,\-–—]?\s*"                          # separator
        rf"({time_re})\s*(?:[–—\-]|to)\s*({time_re})"
        r"(?:\s+[A-Z]{2,4})?$"                        # optional timezone e.g. CST
    )
    match = re.match(pattern, cleaned)
    if not match:
        raise ValueError(f"String format not recognized: {text!r}")

    date_str, start_time, end_time = match.groups()
    return {
        "date": date_str.replace(".", ""),
        "start_time": _normalize_time(start_time),
        "end_time": _normalize_time(end_time),
    }


def parse_class_date(date_str: str) -> Optional[date]:
    """'October 6, 2026' -> date(2026, 10, 6). Returns None if not parseable."""
    s = (date_str or "").replace(",", "").strip()
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


_CITY_LINE_RE = re.compile(
    r"^(?P<city>[A-Za-z][A-Za-z .'\-]*?),\s*(?P<state>[A-Za-z]{2})\.?(?:\s+(?P<zip>\d{5}(?:-\d{4})?))?$"
)
_STREET_SUFFIX = (r"Street|St|Lane|Ln|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Court|Ct|Place|Pl|"
                  r"Square|Sq|Terrace|Ter|Parkway|Pkwy|Highway|Hwy|Circle|Cir|Way|Trail|Trl|Pike|Plaza|Loop|Run")
_UNIT = r"(?:\s+(?:Ste|Suite|Apt|Apartment|Unit|Bldg|Building|Rm|Room|Fl|Floor|#)\.?\s*#?\s*[A-Za-z0-9\-]+)*"
# One-line form: "<site> <street> <city>, <ST> <zip>"
_ONE_LINE_RES = [
    # street ends with a known suffix (+ optional suite) - most reliable
    re.compile(rf"^(?P<site>.*?)\s*(?P<street>\d+[A-Za-z]?\s+.*?\b(?:{_STREET_SUFFIX})\b\.?{_UNIT})\s+"
               r"(?P<city>[A-Za-z][A-Za-z .'\-]*?),\s*(?P<state>[A-Za-z]{2})\.?(?:\s+(?P<zip>\d{5}(?:-\d{4})?))?$",
               re.IGNORECASE),
    # fallback: street = everything from the first house number up to the city
    re.compile(r"^(?P<site>.*?)\s*(?P<street>\d+[A-Za-z]?\s+.*\d[A-Za-z0-9\-]*|\d+[A-Za-z]?\s+\S+(?:\s+\S+)?)\s+"
               r"(?P<city>[A-Za-z][A-Za-z .'\-]*?),\s*(?P<state>[A-Za-z]{2})\.?(?:\s+(?P<zip>\d{5}(?:-\d{4})?))?$"),
]


def parse_location(text: str) -> Optional[dict]:
    """Parse the EnrollNationwide location block into site / street / city / state / zip.

    Handles all of these:
      'Site · 640 Spence Lane Ste 125 · Nashville, TN 37217'                 (old format)
      'Nashville, TN\\n640 Spence Lane Ste 125\\nNashville, TN 37217'          (multi-line)
      'Nashville, TN 640 Spence Lane Ste 125 Nashville, TN 37217\\n640 Spence Lane Ste 125\\nNashville, TN 37217'
      'Nashville, TN 640 Spence Lane Ste 125 Nashville, TN 37217'            (single line)
    Returns None if it can't be parsed confidently.
    """
    raw = (text or "").replace("\xa0", " ").replace(" ", " ")
    lines = [re.sub(r"\s+", " ", l).strip(" ·") for l in raw.splitlines()]
    lines = [l for l in lines if l]
    if not lines:
        return None

    def result(site, street, city_line_match):
        return {
            "training_site": (site or "").strip(" ,·-"),
            "address": street.strip(" ,"),
            "city": city_line_match.group("city").strip(),
            "state": (city_line_match.group("state") or "").upper(),
            "zip": city_line_match.group("zip") or "",
        }

    # 1) old '·' separated format
    joined = " ".join(lines)
    if "·" in joined:
        parts = [p.strip() for p in joined.split("·") if p.strip()]
        if len(parts) >= 3:
            m = _CITY_LINE_RE.match(parts[-1])
            if m:
                return result(parts[0], parts[-2], m)
            return {"training_site": parts[0], "address": parts[1],
                    "city": parts[2].split(",")[0].strip(), "state": "", "zip": ""}

    # 2) multi-line: last line = 'City, ST ZIP', line before = street
    if len(lines) >= 2:
        m = _CITY_LINE_RE.match(lines[-1])
        street = lines[-2]
        if m and re.match(r"^\d", street):
            head = lines[0] if len(lines) >= 3 else ""
            # first line may be the whole block on one line - keep only the part before the street
            if street in head:
                head = head.split(street, 1)[0]
            elif head:
                one = _match_one_line(head)
                if one:
                    head = one["training_site"]
            return result(head, street, m)

    # 3) everything on one line
    one = _match_one_line(lines[0])
    if one:
        return one

    # 4) incomplete address (no street), e.g. 'Brentwood TN office\nBrentwood, TN':
    #    use the first line as the training-site name, keep the city if a 'City, ST' line exists
    site = lines[0]
    city_match = next((m for m in (_CITY_LINE_RE.match(l) for l in lines) if m), None)
    logger.warning(f"Location has no street address - using the first line as the training site: {site!r}")
    return {
        "training_site": site.strip(" ,·-"),
        "address": "",
        "city": city_match.group("city").strip() if city_match else "",
        "state": (city_match.group("state") or "").upper() if city_match else "",
        "zip": (city_match.group("zip") or "") if city_match else "",
    }


def _match_one_line(line: str) -> Optional[dict]:
    for rx in _ONE_LINE_RES:
        m = rx.match(line)
        if m and m.group("street").strip():
            return {
                "training_site": m.group("site").strip(" ,·-"),
                "address": m.group("street").strip(" ,"),
                "city": m.group("city").strip(),
                "state": m.group("state").upper(),
                "zip": m.group("zip") or "",
            }
    return None


def split_name(full_name: str) -> tuple:
    """EnrollNationwide shows names as 'Last First' (e.g. 'Smith John').

    Returns (first_name, last_name): first = last word, last = first word.
    'Smith John' -> ('John', 'Smith'); 'Smith, John' -> ('John', 'Smith').
    Single word -> (word, word).
    """
    parts = (full_name or "").replace(",", " ").split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[-1], parts[0]


def remove_zip_code(text: str) -> str:
    return re.sub(r"\s+\d{5}(?:-\d{4})?$", "", (text or "").strip())


def clean_string(text: str) -> str:
    """Remove non-alphanumeric characters (except spaces and '&') and collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9 &]", "", text or "")).strip()


def expand_address(address: str) -> str:
    abbreviations = {
        'st': 'Street', 'ln': 'Lane', 'ave': 'Avenue', 'blvd': 'Boulevard',
        'rd': 'Road', 'dr': 'Drive', 'ct': 'Court', 'pl': 'Place',
        'sq': 'Square', 'ter': 'Terrace', 'pkwy': 'Parkway', 'hwy': 'Highway',
        'cir': 'Circle', 'apt': 'Apartment', 'ste': 'Suite'
    }
    pattern = r'\b(' + '|'.join(abbreviations.keys()) + r')\b\.?'
    return re.sub(pattern, lambda m: abbreviations[m.group(1).lower()], address or "", flags=re.IGNORECASE)


def generate_redcross_url(email: str, cert_ids: list) -> str:
    """Build the Red Cross QR/certificate URL. Returns '' if there are no cert IDs."""
    if isinstance(cert_ids, str):
        cert_ids = [cert_ids]
    cert_ids = [c.strip() for c in (cert_ids or []) if c and c.strip()]
    if not email or not cert_ids:
        return ""
    params = {
        "email": email,
        "selectedCerts": ",".join(cert_ids) + ",",
        "size": 1185
    }
    return f"https://www.redcross.org/take-a-class/qrcode?{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# Progress state: remembers how far each class got, so a crash or rerun never
# submits (and pays for) the same class on Red Cross twice.
# ---------------------------------------------------------------------------
def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        # Never silently start from scratch if the file is corrupt - that could cause duplicate submissions.
        backup = STATE_FILE + f".corrupt-{int(time.time())}"
        try:
            os.replace(STATE_FILE, backup)
        except OSError:
            pass
        logger.error(f"State file was unreadable ({e}); moved to {backup}. Review it before rerunning.")
        raise


def save_state(state: dict) -> None:
    """Atomic write so a crash mid-save can't corrupt the file."""
    fd, tmp = tempfile.mkstemp(dir=BASE_DIR, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        logger.error(f"Failed to save state: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass

# ---------------------------------------------------------------------------
# Failed classes file + Discord notifications
# ---------------------------------------------------------------------------
FAILED_CLASSES_FILE = os.path.join(BASE_DIR, "failed_classes.txt")
DISCORD_MAX_LEN = 1900   # Discord limit is 2000 characters per message


def record_failed_class(url: str) -> None:
    """Add a class URL to failed_classes.txt (once - no duplicates). You remove lines manually."""
    try:
        if url in set(get_failed_classes()):
            return
        with open(FAILED_CLASSES_FILE, "a", encoding="utf-8") as f:
            f.write(url.strip() + "\n")
    except Exception as e:
        logger.error(f"Could not write to {FAILED_CLASSES_FILE}: {e}")


def get_failed_classes() -> list:
    try:
        with open(FAILED_CLASSES_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"Could not read {FAILED_CLASSES_FILE}: {e}")
        return []


def _chunks(text: str, size: int = DISCORD_MAX_LEN) -> list:
    """Split a long message on line breaks so each piece fits in one Discord message."""
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            parts.append(line[:size]); line = line[size:]
        if len(current) + len(line) > size:
            parts.append(current); current = ""
        current += line
    if current.strip():
        parts.append(current)
    return parts


def send_discord_message(message: str) -> bool:
    """Post a message to the Discord webhook in DISCORD_WEBHOOK_URL (.env). Never raises."""
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        logger.warning("DISCORD_WEBHOOK_URL is not set - Discord message not sent")
        return False

    import urllib.request
    import urllib.error
    ok = True
    for part in _chunks(message):
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    webhook,
                    data=json.dumps({"content": part}).encode("utf-8"),
                    # Discord rejects requests without a normal User-Agent
                    headers={"Content-Type": "application/json", "User-Agent": "ClassAutomation/1.0"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15):
                    pass
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:   # rate limited - wait and retry
                    try:
                        wait = float(json.loads(e.read().decode()).get("retry_after", 2))
                    except Exception:
                        wait = 2
                    time.sleep(min(wait, 30))
                    continue
                logger.error(f"Discord webhook error {e.code}: {e.reason}")
                ok = False
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(2)
                    continue
                logger.error(f"Could not send Discord message: {e}")
                ok = False
        time.sleep(0.5)
    return ok