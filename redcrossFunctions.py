import re
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from pageSelectors import (
    RedCrossLoginPage, QuickLinks,
    RedCrossClassListingPage, RCCertificatesPage
)
from utils import (
    logger, wait_while_element_is_displaying,
    input_element, check_element_exists, click_element_by_js,
    select_by_text, safe_navigate_to_url, remove_zip_code, clean_string,
    click_element, input_element_by_typing, expand_address,
    generate_redcross_url, select_by_value, checkbox_is_checked,
    wait_for_element_to_disappear, wait_for_page_load,
)

ORGANIZATION = 'Code Blue CPR Services LLC'
TIME_ZONE = 'US/Central'

# Submission results for accept_agreement_and_finalize
NOT_SUBMITTED = "not_submitted"   # failed before payment - safe to retry from scratch
SUBMITTED = "submitted"           # final submit clicked and the form closed
UNCONFIRMED = "unconfirmed"       # payment clicked but final result unclear - never auto-resubmit


class StepFailed(Exception):
    """Raised when a required step fails, so we stop before submitting bad data."""


def _require(ok, step: str):
    if not ok:
        raise StepFailed(step)


def login_on_redcross(driver, username, password) -> bool:
    try:
        if not safe_navigate_to_url(driver, QuickLinks.redcross):
            return False

        if check_element_exists(driver, RedCrossLoginPage.login_button, timeout=5):
            _require(click_element_by_js(driver, RedCrossLoginPage.login_button), "click Login link")
            _require(input_element_by_typing(driver, RedCrossLoginPage.input_("username"), username, timeout=20), "enter username")
            _require(input_element_by_typing(driver, RedCrossLoginPage.input_("password"), password), "enter password")
            time.sleep(1)
            _require(click_element(driver, RedCrossLoginPage.sign_in_button), "click LOGIN")

        if check_element_exists(driver, RedCrossLoginPage.nathan_profile, timeout=30):
            logger.info("Successfully logged into Red Cross")
            return True

        logger.error(f"Red Cross login could not be confirmed. Current URL: {driver.current_url}")
        return False

    except StepFailed as e:
        logger.error(f"Red Cross login failed at step: {e}")
        return False
    except Exception as e:
        logger.error(f"Error occurred while logging in to Red Cross: {e}")
        return False


def get_lookup_error(driver, wait_seconds: float = 3.0) -> str:
    """Return the text of any visible div.lookupError on the page ('' if there is none)."""
    deadline = time.time() + wait_seconds
    while True:
        texts = []
        for el in driver.find_elements(*RedCrossClassListingPage.lookup_error):
            try:
                if el.is_displayed():
                    text = " ".join((el.text or "").split())
                    if text and text not in texts:
                        texts.append(text)
            except StaleElementReferenceException:
                continue
        if texts or time.time() >= deadline:
            return " | ".join(texts)
        time.sleep(0.5)


def add_class_info(class_info, driver) -> bool:
    try:
        _require(click_element_by_js(driver, RedCrossClassListingPage.course_record_entry_btn, timeout=20),
                 "open Course Record Entry")

        # Organization
        _require(input_element(driver, RedCrossClassListingPage.search_input(1), ORGANIZATION), "enter organization")
        time.sleep(1)
        _require(click_element_by_js(driver, RedCrossClassListingPage.first_option(ORGANIZATION), timeout=5, max_retries=3),
                 f"select organization '{ORGANIZATION}'")

        # Course
        sku = class_info.get('sku', '')
        _require(input_element(driver, RedCrossClassListingPage.course_input, sku), f"enter course SKU {sku}")
        time.sleep(1)
        _require(click_element(driver, RedCrossClassListingPage.course_search_button), "click course search")
        _require(click_element_by_js(driver, RedCrossClassListingPage.course_select_btn, timeout=20),
                 f"select course for SKU {sku} (no search result?)")

        # Dates
        class_date = class_info.get('class_date', '')
        for label in ('Instructor-Led Session Start Date', 'Instructor-Led Session End Date', 'Date'):
            _require(input_element(driver, RedCrossClassListingPage.session_input(label), class_date), f"enter '{label}'")

        # Times
        _require(input_element(driver, RedCrossClassListingPage.session_input('Start Time'), class_info.get('start_time', '')), "enter Start Time")
        _require(input_element(driver, RedCrossClassListingPage.session_input('End Time'), class_info.get('end_time', '')), "enter End Time")
        _require(select_by_text(driver, RedCrossClassListingPage.time_zone_select, TIME_ZONE), f"select time zone {TIME_ZONE}")

        # Red Cross shows div.lookupError when the date/time/time zone isn't accepted -> skip this class
        lookup_error = get_lookup_error(driver)
        if lookup_error:
            logger.error(f"Red Cross error after entering date/time: {lookup_error}")
            return False

        # Participants
        _require(input_element(driver, RedCrossClassListingPage.session_input('Total Number of Participants'),
                               class_info.get('enrolled_students', '')), "enter number of participants")

        # Instructor
        instructor_name = class_info.get('instructor_name', '')
        _require(input_element_by_typing(driver, RedCrossClassListingPage.search_input(1), instructor_name), "enter instructor")
        time.sleep(1)
        _require(click_element_by_js(driver, RedCrossClassListingPage.first_option(instructor_name), timeout=5, max_retries=3),
                 f"select instructor '{instructor_name}' (name must match Red Cross exactly)")

        # Location
        _require(add_location_info(class_info.get('location', {}), driver), "set location")

        _require(click_element_by_js(driver, RedCrossClassListingPage.submit_button('Next')), "click Next (class page)")

        # FIX: confirm the student page actually opened (page validation errors keep you on the same page)
        _require(check_element_exists(driver, RedCrossClassListingPage.student_input('First Name', 1), timeout=30),
                 "student entry page did not load - check the class page for a validation error")
        return True

    except StepFailed as e:
        logger.error(f"Adding class info failed at step: {e}")
        return False
    except Exception as e:
        logger.error(f"Error occurred while adding class info: {e}")
        return False


# ---------------------------------------------------------------------------
# Location selection (scored matching against the options shown in the dropdown)
# ---------------------------------------------------------------------------
# "Most" of the training-site words must be in the option's <b> text: more than this share.
TS_MOST_RATIO = 0.5

# Street words that say nothing about WHICH location it is - ignored when comparing addresses
ADDRESS_GENERIC_WORDS = {
    "street", "lane", "avenue", "boulevard", "road", "drive", "court", "place", "square", "terrace",
    "parkway", "highway", "circle", "way", "trail", "pike", "plaza", "loop", "run", "suite", "apartment",
    "unit", "bldg", "building", "floor", "room", "north", "south", "east", "west", "n", "s", "e", "w",
}


# Generic words in training-site names. They often differ between EnrollNationwide and Red Cross
# ("ABC CPR Training Services" vs "ABC CPR"), so a second search is done without them and they
# are ignored when comparing site names. Add more words here if needed (lower case).
TS_GENERIC_WORDS = {
    "services", "service", "training", "trainings", "center", "centre", "llc", "inc", "co",
    "company", "corp", "corporation", "ltd", "group",
}


def _strip_generic_ts_words(ts: str) -> str:
    """'Code Blue CPR Services' -> 'Code Blue CPR' (keeps the original if nothing would be left)."""
    kept = [w for w in ts.split() if w.lower().strip(".,") not in TS_GENERIC_WORDS]
    while kept and kept[-1] == "&":      # don't leave a dangling '&' ('ABC & Training' -> 'ABC')
        kept.pop()
    return " ".join(kept) if kept else ts


def _words(text: str) -> list:
    """Lower-case words with punctuation removed and street abbreviations expanded
    ('Ln' == 'Lane', 'Ste' == 'Suite'), so both sides are compared the same way."""
    spaced = re.sub(r"[^A-Za-z0-9]+", " ", text or "")   # newlines/punctuation -> spaces (not glued)
    return [w for w in expand_address(spaced).lower().split() if w]


def _read_displayed_options(driver, wait_seconds: float = 6.0) -> list:
    """Return [(element, b_text, full_text)] for every dropdown option currently visible."""
    deadline = time.time() + wait_seconds
    options = []
    while time.time() < deadline:
        options = []
        for el in driver.find_elements(*RedCrossClassListingPage.location_options):
            try:
                if not el.is_displayed():
                    continue
                full_text = (el.text or "").strip()
                b_elems = el.find_elements(By.XPATH, ".//b")
                b_text = " ".join((b.text or "").strip() for b in b_elems).strip()
                if not b_text:                       # no <b>: treat the first line as the name
                    b_text = full_text.split("\n")[0]
                if full_text:
                    options.append((el, b_text, full_text))
            except StaleElementReferenceException:
                continue
        if options:
            break
        time.sleep(0.5)
    return options


def _address_matches(addr_words: set, option_words: set) -> tuple:
    """Does the option show the same street address?

    - If the address has a house number (e.g. 640), that number must appear in the option
      AND at least one street-name word (e.g. 'spence') must appear too.
    - If there's no house number, at least half of the address words must appear.
    Returns (matched: bool, hits: set)
    """
    hits = addr_words & option_words
    numbers = {w for w in addr_words if w.isdigit()}
    names = addr_words - numbers
    house_no = _HOUSE_NO.get("value")
    if house_no:
        ok = house_no in option_words and (bool(names & option_words) if names else True)
    else:
        ok = bool(addr_words) and len(hits) / len(addr_words) >= 0.5
    return ok, hits


_HOUSE_NO = {"value": None}   # set per class in add_location_info
_CITY_WORDS = {"value": set()}  # set per class in add_location_info


def _score_option(ts_words: set, addr_words: set, b_text: str, full_text: str) -> dict:
    b_words = set(_words(b_text))
    all_words = set(_words(full_text))
    ts_hits = ts_words & b_words
    ts_ratio = len(ts_hits) / len(ts_words) if ts_words else 0.0
    site_ok = ts_ratio > TS_MOST_RATIO
    addr_ok, addr_hits = _address_matches(addr_words, all_words)
    addr_ratio = len(addr_hits) / len(addr_words) if addr_words else 0.0
    return {"site_ok": site_ok, "addr_ok": addr_ok, "score": ts_ratio * 2 + addr_ratio,
            "ts_hits": sorted(ts_hits), "addr_hits": sorted(addr_hits)}


def _choose_location_option(options: list, ts_words: set, addr_words: set):
    """Pick the option whose site name AND address both match.
    Returns (element, reason) or (None, None) - None means 'create a new location'."""
    if not options:
        return None, None

    scored = []
    for el, b_text, full_text in options:
        s = _score_option(ts_words, addr_words, b_text, full_text)
        scored.append((el, b_text, full_text, s))
        logger.info(f"  option: {full_text.replace(chr(10), ' | ')!r} -> "
                    f"site {'OK' if s['site_ok'] else 'no'} {s['ts_hits']}, "
                    f"address {'OK' if s['addr_ok'] else 'no'} {s['addr_hits']}")

    # No street address on the class: only the site name can be compared
    if not addr_words:
        city_words = _CITY_WORDS["value"]
        qualifying = [x for x in scored if x[3]["site_ok"]]
        if not qualifying:
            logger.info("No option matches the training site name")
            return None, None
        # best site-name match; if tied, prefer an option that also shows the city
        def rank(x):
            # look for the city only in the option's address part, not in its name
            address_part = x[2].replace(x[1], " ", 1)
            in_city = bool(city_words) and city_words <= set(_words(address_part))
            return (x[3]["score"], in_city)
        qualifying.sort(key=rank, reverse=True)
        best = qualifying[0]
        if len(qualifying) > 1 and rank(qualifying[1]) == rank(best):
            logger.warning("Several options matched the site name equally well; taking the first one shown")
        return best[0], "site name matches (class has no street address)"

    # The site name must match (checked first) AND the address must match
    qualifying = [x for x in scored if x[3]["site_ok"] and x[3]["addr_ok"]]
    if qualifying:
        qualifying.sort(key=lambda x: x[3]["score"], reverse=True)
        best = qualifying[0]
        if len(qualifying) > 1 and qualifying[1][3]["score"] == best[3]["score"]:
            logger.warning("Several options matched equally well; taking the first one shown")
        return best[0], "site name and address match"

    # Only one option shown: accept it if at least the site name OR the address matches
    if len(scored) == 1:
        el, b_text, full_text, s = scored[0]
        if s["site_ok"] or s["addr_ok"]:
            return el, "only option shown and " + ("site name" if s["site_ok"] else "address") + " matches"
        logger.info("The only option shown matches neither the site name nor the address - not selecting it")
        return None, None

    logger.info("No option matches both the site name and the address")
    return None, None


def _click_option(driver, element) -> bool:
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        time.sleep(0.3)
        try:
            element.click()
        except Exception:
            driver.execute_script("arguments[0].click();", element)
        time.sleep(1)
        return True
    except Exception as e:
        logger.error(f"Could not click location option: {e}")
        return False


def search_location_and_select(driver, search_text, ts_words: set, addr_words: set) -> bool:
    """Type search_text into the location search box and select the option that fits the rules."""
    try:
        if not search_text:
            return False
        if not input_element_by_typing(driver, RedCrossClassListingPage.search_input(2), search_text):
            return False
        time.sleep(1.5)  # let the dropdown populate

        options = _read_displayed_options(driver)
        logger.info(f"Location search '{search_text}' shows {len(options)} option(s)")
        element, reason = _choose_location_option(options, ts_words, addr_words)
        if element is None:
            return False

        chosen_text = ""
        try:
            chosen_text = element.text.strip().replace("\n", " | ")
        except Exception:
            pass
        if _click_option(driver, element):
            logger.info(f"Selected location: {chosen_text!r} ({reason})")
            return True
        return False
    except Exception as e:
        logger.error(f"Error occurred while searching location: {e}")
        return False


def add_location_info(location, driver) -> bool:
    try:
        raw_ts = (location.get('training_site', '') or '').strip()
        raw_address = (location.get('address', '') or '').strip()
        city = (location.get('city', '') or '').strip()
        ts = clean_string(raw_ts)
        address = expand_address(clean_string(raw_address))

        if not ts:
            logger.error(f"Location has no training site name: {location}")
            return False

        ts_core = _strip_generic_ts_words(ts)
        # compare site names on the distinctive words only (fall back to all words if nothing is left)
        ts_words = (set(_words(raw_ts)) - TS_GENERIC_WORDS) or set(_words(raw_ts))
        addr_words = set(_words(raw_address)) - ADDRESS_GENERIC_WORDS
        m = re.match(r"^\s*(\d+)", raw_address)
        _HOUSE_NO["value"] = m.group(1) if m else None
        _CITY_WORDS["value"] = set(_words(city))
        logger.info(f"Matching location -> site words: {sorted(ts_words)}, address words: {sorted(addr_words)}")

        # 1) search by the full training site name
        # 2) search by the site name without generic words ('Services', 'Training', ...)
        # 3) search by the address (if the class has one)
        searches = [ts]
        if ts_core and ts_core.lower() != ts.lower():
            searches.append(ts_core)
        searches.append(address)
        for search_text in searches:
            if search_text and search_location_and_select(driver, search_text, ts_words, addr_words):
                return True

        if not address:
            logger.error(f"No existing location matches '{raw_ts}' and the class has no street address, "
                         f"so a new location can't be created - process this class manually")
            return False

        # 3) nothing fits -> create a new location
        logger.info("No existing location matched - creating a new location")
        return create_new_location(driver, ts, raw_address, city)

    except StepFailed as e:
        logger.error(f"Adding location failed at step: {e}")
        return False
    except Exception as e:
        logger.error(f"Error occurred while adding location info: {e}")
        return False


def _close_site_dropdown(driver):
    """Close the site search dropdown so it doesn't cover the new-location fields."""
    try:
        el = driver.find_element(*RedCrossClassListingPage.search_input(2))
        el.send_keys(Keys.ESCAPE)
        driver.execute_script("arguments[0].blur();", el)
    except Exception:
        pass
    time.sleep(0.5)


def create_new_location(driver, ts: str, raw_address: str, city: str) -> bool:
    """Create a new Red Cross location from the class address."""
    try:
        if not city:
            logger.error("City missing; cannot create new location")
            return False
        _close_site_dropdown(driver)

        street = re.split(r"\b(?:ste|suite|apt|apartment|unit|bldg|building|rm|room|fl|floor|#)\b",
                          clean_string(raw_address), flags=re.IGNORECASE)[0].strip()
        query = f"{street} {city}".strip()
        site_name = f"{ts} {remove_zip_code(city)}"

        _require(input_element_by_typing(driver, RedCrossClassListingPage.address_search, query),
                 "type address into 'Address Search'")
        time.sleep(2)   # wait for address suggestions

        # Pick the suggestion with the house number + street, else one with the city, else the first one
        m = re.match(r"^(\d+)\s+(\S+)", street)
        candidates = []
        if m:
            candidates.append(f"{m.group(1)} {m.group(2)}")
        candidates.append(city)
        picked = False
        for text in candidates:
            if click_element_by_js(driver, RedCrossClassListingPage.address_option(text), timeout=4, max_retries=1):
                logger.info(f"Picked address suggestion containing '{text}'")
                picked = True
                break
        if not picked and click_element_by_js(driver, RedCrossClassListingPage.address_first_option, timeout=4, max_retries=1):
            logger.warning("Picked the first address suggestion (no suggestion contained the street or city)")
            picked = True
        _require(picked, f"no address suggestion appeared for '{query}'")

        _require(input_element(driver, RedCrossClassListingPage.site_name_input, site_name), "enter site name")
        logger.info(f"New location filled in: '{site_name}' at '{query}'")
        return True

    except StepFailed as e:
        logger.error(f"Creating new location failed at step: {e}")
        return False
    except Exception as e:
        logger.error(f"Error while creating new location: {e}")
        return False


def add_students_info(driver, students_data: list) -> bool:
    try:
        _require(students_data, "no students to add")
        for idx, student in enumerate(students_data, start=1):  # FIX: XPath indexes start at 1, not 0
            who = student.get('name') or student.get('email')
            _require(input_element(driver, RedCrossClassListingPage.student_input('First Name', idx), student.get('first_name', '')),
                     f"first name for {who} (row {idx})")
            _require(input_element(driver, RedCrossClassListingPage.student_input('Last Name', idx), student.get('last_name', '')),
                     f"last name for {who} (row {idx})")
            _require(input_element(driver, RedCrossClassListingPage.student_input('Email', idx), student.get('email', '')),
                     f"email for {who} (row {idx})")
            if student.get('phone'):
                input_element(driver, RedCrossClassListingPage.student_input('Phone Number', idx), student['phone'])

            evaluation = student.get('result', '')
            _require(select_by_text(driver, RedCrossClassListingPage.evaluation_select('Evaluation', idx), evaluation),
                     f"evaluation '{evaluation}' for {who}")
            if evaluation != 'Successful':
                # Optional: only some evaluations show a reason dropdown
                select_by_value(driver, RedCrossClassListingPage.evaluation_select('Unsuccessful', idx),
                                "Did not attend all course sessions", timeout=3)

        _require(click_element_by_js(driver, RedCrossClassListingPage.submit_button('Next')), "click Next (students page)")
        wait_while_element_is_displaying(driver, By.CSS_SELECTOR, "span.progressMsg", timeout=30)
        if check_element_exists(driver, RedCrossClassListingPage.proceed_button, timeout=2):
            click_element(driver, RedCrossClassListingPage.proceed_button)
        _require(check_element_exists(driver, RedCrossClassListingPage.legal_agreement_btn, timeout=30)
                 or WebDriverWait(driver, 5).until(EC.presence_of_element_located(RedCrossClassListingPage.legal_agreement_btn)),
                 "agreement page did not load - check the students page for a validation error")
        return True

    except StepFailed as e:
        logger.error(f"Adding students failed at step: {e}")
        return False
    except Exception as e:
        logger.error(f"Error occurred while adding students info: {e}")
        return False


def accept_agreement_and_finalize(driver, mark_stage=lambda stage: None) -> str:
    """Accept agreement, pay, submit.

    mark_stage('submitting') is called right BEFORE the payment click so that, if the
    script crashes afterwards, the next run knows not to submit/pay for this class again.
    Returns NOT_SUBMITTED, SUBMITTED or UNCONFIRMED.
    """
    try:
        # FIX: a JS click toggles - if the box was already ticked it would untick it
        if not checkbox_is_checked(driver, RedCrossClassListingPage.legal_agreement_btn, timeout=10):
            _require(click_element_by_js(driver, RedCrossClassListingPage.legal_agreement_btn), "tick agreement checkbox")
        _require(checkbox_is_checked(driver, RedCrossClassListingPage.legal_agreement_btn, timeout=5), "agreement checkbox not ticked")
        _require(click_element_by_js(driver, RedCrossClassListingPage.submit_button('Next')), "click Next (agreement page)")

        _require(click_element_by_js(driver, RedCrossClassListingPage.invoice_btn, timeout=30), "open Invoice")
        _require(check_element_exists(driver, RedCrossClassListingPage.payment_btn, timeout=30), "Submit Payment button not shown")
    except StepFailed as e:
        logger.error(f"Finalizing failed before payment at step: {e}")
        return NOT_SUBMITTED
    except Exception as e:
        logger.error(f"Error before payment: {e}")
        return NOT_SUBMITTED

    # ---- point of no return ----
    mark_stage("submitting")
    try:
        if not click_element_by_js(driver, RedCrossClassListingPage.payment_btn, max_retries=1):
            logger.error("Could not click 'Submit Payment' (nothing was paid)")
            mark_stage(None)
            return NOT_SUBMITTED

        submit_locator = RedCrossClassListingPage.submit_button('Submit')
        if not click_element_by_js(driver, submit_locator, timeout=30, max_retries=1):
            logger.error("Payment clicked but final 'Submit' not found - check Red Cross manually")
            return UNCONFIRMED

        wait_for_page_load(driver)
        if wait_for_element_to_disappear(driver, submit_locator, timeout=45):
            logger.info("Class submitted on Red Cross")
            return SUBMITTED
        logger.warning("Submit clicked but the form is still showing; will confirm via certificates")
        return UNCONFIRMED
    except Exception as e:
        logger.error(f"Error after payment step: {e}")
        return UNCONFIRMED


def _lookup_certs_for_email(driver, email: str) -> list:
    """Search the public certificate page for one email and return cert IDs."""
    # FIX: reload the search page every time - after a search the input may no longer be on the page
    if not safe_navigate_to_url(driver, QuickLinks.find_certificate):
        return []
    if not input_element(driver, RCCertificatesPage.search_input, email, timeout=20):
        return []
    if not click_element(driver, RCCertificatesPage.search_button):
        return []
    # FIX: wait for results instead of reading them instantly
    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located(RCCertificatesPage.cert_id))
    except Exception:
        return []
    ids = []
    for cert in driver.find_elements(*RCCertificatesPage.cert_id):
        value = (cert.get_attribute("value") or "").strip()
        if value and value not in ids:
            ids.append(value)
    return ids


def get_cert_links(driver, std_data: list, max_attempts: int = 4, wait_between: int = 60) -> dict:
    """Return {email: certificate_url} for every successful student whose certificate was found.

    Certificates can take a few minutes to appear after submission, so missing ones are
    retried. Students not marked 'Successful' don't get certificates and are skipped.
    """
    targets = [s.get('email', '').strip() for s in std_data
               if s.get('email') and s.get('result') == 'Successful']
    found = {}
    if not targets:
        logger.warning("No successful students with an email - no certificates to fetch")
        return found

    for attempt in range(1, max_attempts + 1):
        for email in targets:
            if email in found:
                continue
            try:
                url = generate_redcross_url(email, _lookup_certs_for_email(driver, email))
                if url:
                    found[email] = url
            except Exception as e:
                logger.warning(f"Certificate lookup error for {email}: {e}")

        missing = [e for e in targets if e not in found]
        if not missing:
            break
        if attempt < max_attempts:
            logger.info(f"Certificates not found yet for {len(missing)} student(s); retrying in {wait_between}s "
                        f"(attempt {attempt}/{max_attempts})")
            time.sleep(wait_between)

    missing = [e for e in targets if e not in found]
    if missing:
        logger.warning(f"No certificate found for: {', '.join(missing)}")
    logger.info(f"Found certificates for {len(found)}/{len(targets)} student(s)")
    return found