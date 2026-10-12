import time
from urllib.parse import urlparse
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from pageSelectors import EnrollNationwideLoginPage, EnrollNationwideRCRostersPage, QuickLinks
from utils import (
    logger,
    input_element,
    get_element_text,
    check_element_exists,
    click_element_by_js,
    parse_event_string,
    safe_navigate_to_url,
    wait_for_page_load,
    accept_alert_if_present,
    split_name,
    parse_location,
)


def login_to_enrollNationwide(driver, username, password) -> bool:
    """Log in to Enroll Nationwide (no-op if the session is still valid). Returns True on success."""
    try:
        if not safe_navigate_to_url(driver, QuickLinks.dashboard):
            logger.error("Failed to navigate to EnrollNationwide dashboard/login page")
            return False

        # Login form visible -> not logged in (session expired or first run)
        if check_element_exists(driver, EnrollNationwideLoginPage.username_input, timeout=5):
            if not input_element(driver, EnrollNationwideLoginPage.username_input, username):
                logger.error("Failed to input username")
                return False
            if not input_element(driver, EnrollNationwideLoginPage.password_input, password):
                logger.error("Failed to input password")
                return False

            # Optional - don't spend 30s retrying if it's not there
            click_element_by_js(driver, EnrollNationwideLoginPage.remember_me_checkbox, timeout=3, max_retries=1)
            time.sleep(1)

            if not click_element_by_js(driver, EnrollNationwideLoginPage.login_button):
                logger.error("Failed to click login button")
                return False
            wait_for_page_load(driver)

        if check_element_exists(driver, EnrollNationwideLoginPage.info_needed_popup, timeout=3):
            click_element_by_js(driver, EnrollNationwideLoginPage.info_needed_popup, timeout=3, max_retries=1)
            logger.info("Clicked on 'Info Needed' popup")
            wait_for_page_load(driver)

        # Verify: the login form must be gone
        if check_element_exists(driver, EnrollNationwideLoginPage.username_input, timeout=3):
            logger.error("Login to EnrollNationwide failed - still on the login page (check ENW_USERNAME/ENW_PASSWORD)")
            return False

        if 'account' not in driver.current_url.lower():
            logger.warning(f"Logged in, but unexpected URL: {driver.current_url}")
        logger.info("Logged into EnrollNationwide")
        return True

    except Exception as e:
        logger.error(f"An error occurred during login to EnrollNationwide: {e}")
        return False


def get_all_rosters_links(driver) -> list | None:
    """Retrieve all class links from the RC Rosters tab. [] = no classes, None = page failed."""
    try:
        if not safe_navigate_to_url(driver, QuickLinks.rc_rosters):
            return None

        if not check_element_exists(driver, EnrollNationwideRCRostersPage.all_classes, timeout=15):
            logger.info("No classes found on the RC Rosters page")
            return []

        class_links = []
        for element in driver.find_elements(*EnrollNationwideRCRostersPage.all_classes):
            href = element.get_attribute("href")
            if href and href not in class_links and not href.lower().startswith("javascript"):
                class_links.append(href)

        logger.info(f"Retrieved {len(class_links)} class links")
        return class_links
    except Exception as e:
        logger.error(f"Failed to retrieve class links: {e}")
        return None


def open_next_class(driver, skip: set) -> tuple:
    """On the class list (RC Rosters), click the FIRST class that hasn't been tried yet in this run.
    Only that one class is opened - nothing is read from the other classes.

    Returns (status, link): ("opened", link) | ("empty", None) | ("none_left", None) | ("error", None)
    """
    try:
        if not safe_navigate_to_url(driver, QuickLinks.rc_rosters):
            return "error", None
        if not check_element_exists(driver, EnrollNationwideRCRostersPage.all_classes, timeout=15):
            return "empty", None

        for element in driver.find_elements(*EnrollNationwideRCRostersPage.all_classes):
            href = element.get_attribute("href")
            if not href or href.lower().startswith("javascript") or href in skip:
                continue
            logger.info(f"Opening class from the list: {element.text.strip() or href}")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'}); arguments[0].click();", element)
            wait_for_page_load(driver)
            # if the click opened nothing (or a new tab), fall back to going to the link directly
            if driver.current_url.rstrip("/") != href.rstrip("/"):
                safe_navigate_to_url(driver, href)
            return "opened", href

        return "none_left", None
    except Exception as e:
        logger.error(f"Failed to open the next class from the list: {e}")
        return "error", None


def open_class_page(driver, class_link, username=None, password=None) -> bool:
    """Open a class page. If the session expired and we land on the login page,
    log in again and reopen the class."""
    already_open = driver.current_url.rstrip("/") == class_link.rstrip("/")
    if not already_open and not safe_navigate_to_url(driver, class_link):
        return False
    if check_element_exists(driver, EnrollNationwideLoginPage.username_input, timeout=2):
        logger.warning("EnrollNationwide session expired - logging in again")
        if not (username and password and login_to_enrollNationwide(driver, username, password)):
            return False
        if not safe_navigate_to_url(driver, class_link):
            return False
    try:
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.XPATH, "//article/div")))
        return True
    except Exception:
        logger.error(f"Class page did not load: {class_link}")
        return False


def get_class_details(driver, class_link, navigate: bool = False) -> dict | None:
    """Read and validate class details from the class page.
    By default it reads the page that is already open (see open_class_page)."""
    try:
        if navigate and not safe_navigate_to_url(driver, class_link):
            return None

        class_date_time_text = get_element_text(driver, EnrollNationwideRCRostersPage.data_table_("Class times"))
        class_date_time = parse_event_string(class_date_time_text)
        class_date = class_date_time.get("date", "")
        start_time = class_date_time.get("start_time", "")
        end_time = class_date_time.get("end_time", "")

        sku = get_element_text(driver, EnrollNationwideRCRostersPage.sku)
        instructor_name = get_element_text(driver, EnrollNationwideRCRostersPage.instructor_name)
        enrolled_text = get_element_text(driver, EnrollNationwideRCRostersPage.enrolled_std)
        enrolled_students = enrolled_text.split('/')[0].strip() if enrolled_text else ""

        class_location = get_element_text(driver, EnrollNationwideRCRostersPage.data_table_("Location"))
        location = parse_location(class_location)
        if not location:
            logger.error(f"Could not parse location text: {class_location!r}")
            return None
        logger.info(f"Parsed location -> site: {location['training_site']!r}, "
                    f"address: {location['address']!r}, city: {location['city']!r}")

        # Student details from the roster table
        std_names = driver.find_elements(*EnrollNationwideRCRostersPage.roster_table("/span/strong"))
        std_emails = driver.find_elements(*EnrollNationwideRCRostersPage.roster_table("/span/small"))
        std_results = driver.find_elements(*EnrollNationwideRCRostersPage.roster_table("[2]/span"))

        # FIX: zip() silently drops students when the lists differ in length,
        # which would pair the wrong result/email with a student.
        if not (len(std_names) == len(std_emails) == len(std_results)):
            logger.error(
                f"Roster columns don't line up (names={len(std_names)}, emails={len(std_emails)}, "
                f"results={len(std_results)}) for {class_link}. Skipping to avoid submitting wrong data."
            )
            return None

        students_data = []
        for name, email, result in zip(std_names, std_emails, std_results):
            full_name = name.text.strip()
            first_name, last_name = split_name(full_name)
            students_data.append({
                "name": full_name,
                "first_name": first_name,   # FIX: Red Cross step reads first/last name; these were never set
                "last_name": last_name,
                "email": email.text.strip(),
                "phone": "",
                "result": "Successful" if result.text.strip() == "Complete" else "Not Evaluated",
            })

        class_details = {
            "sku": sku,
            "class_date": class_date,
            "start_time": start_time,
            "end_time": end_time,
            "instructor_name": instructor_name,
            "enrolled_students": enrolled_students,
            "location": location,
            "students_data": students_data,
        }

        # ---- validation: never send half-empty data to Red Cross ----
        missing = [k for k in ("sku", "class_date", "start_time", "end_time", "instructor_name") if not class_details[k]]
        # the street address may be missing (e.g. 'Brentwood TN office / Brentwood, TN') - the site name is enough
        missing += [f"location.{k}" for k in ("training_site",) if not class_details["location"].get(k)]
        if not students_data:
            missing.append("students")
        bad_students = [s["name"] or "(no name)" for s in students_data if not (s["first_name"] and s["email"])]
        if bad_students:
            missing.append(f"name/email for: {', '.join(bad_students)}")
        if missing:
            logger.error(f"Class {class_link} is missing required data: {'; '.join(missing)}")
            return None

        if enrolled_students and enrolled_students.isdigit() and int(enrolled_students) != len(students_data):
            logger.warning(f"Header says {enrolled_students} enrolled but roster lists {len(students_data)} students; using roster count")
        class_details["enrolled_students"] = str(len(students_data))

        return class_details

    except Exception as e:
        logger.error(f"Failed to retrieve details for class at {class_link}: {e}")
        return None


def add_cert_links_to_students(driver, class_link: str, cert_links: dict) -> bool:
    """Paste cert links ({email: url}) into the class page that is ALREADY OPEN, then save.
    Returns True only if every link was entered and saved."""
    try:
        if not cert_links:
            logger.warning("No certificate links to add")
            return False

        # We should still be on the class page. Only reopen it if the link fields aren't there
        # (e.g. the page was refreshed or the session dropped while we were on Red Cross).
        first_email = next(iter(cert_links))
        if not check_element_exists(driver, EnrollNationwideRCRostersPage.cert_link_input(first_email), timeout=5):
            logger.warning("Certificate fields not found on the current page - reopening the class page")
            if not safe_navigate_to_url(driver, class_link):
                return False

        failed = []
        for email, cert_link in cert_links.items():
            if not cert_link:
                failed.append(email)
                continue
            if input_element(driver, EnrollNationwideRCRostersPage.cert_link_input(email), cert_link):
                logger.info(f"Pasted certificate link for {email}")
            else:
                logger.error(f"Could not find/fill certificate link field for {email}")
                failed.append(email)

        if len(failed) == len(cert_links):
            logger.error("No certificate links could be entered; not saving")
            return False

        # Check what's in the fields before saving
        for email, cert_link in cert_links.items():
            if email in failed:
                continue
            elems = driver.find_elements(*EnrollNationwideRCRostersPage.cert_link_input(email))
            if not elems or (elems[0].get_attribute("value") or "").strip() != cert_link:
                logger.error(f"Certificate link field for {email} does not contain the pasted link")
                failed.append(email)

        if not click_element_by_js(driver, EnrollNationwideRCRostersPage.save_links_button):
            logger.error("Could not click 'Save & email'")
            return False
        accept_alert_if_present(driver)
        wait_for_page_load(driver)
        time.sleep(2)

        if failed:
            logger.error(f"Certificate links not saved for: {', '.join(failed)}")
            return False
        logger.info(f"Saved {len(cert_links)} certificate link(s)")
        return True
    except Exception as e:
        logger.error(f"Failed to add certificate links to students: {e}")
        return False


def class_left_rosters_list(driver, class_link) -> bool:
    """After 'Save & email' the site marks the class finished automatically.
    Open the rosters list and confirm the class is no longer on it."""
    try:
        if not safe_navigate_to_url(driver, QuickLinks.rc_rosters):
            return False
        parsed = urlparse(class_link)
        rel_href = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        if check_element_exists(driver, EnrollNationwideRCRostersPage.class_in_list(class_link, rel_href), timeout=3):
            logger.error(f"Class is still on the rosters list after saving the links: {class_link}")
            return False
        logger.info("Class is no longer on the rosters list - finished")
        return True
    except Exception as e:
        logger.error(f"Could not check the rosters list for {class_link}: {e}")
        return False