from selenium.webdriver.common.by import By

BASE_URL = "https://enrollnationwide.com"


def xpath_literal(value) -> str:
    """Safely quote a value for use inside an XPath expression.

    Names like O'Brien or emails like o'neil@x.com would otherwise break
    a selector written as text()= '{x}'.
    """
    value = "" if value is None else str(value)
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


_q = xpath_literal


class QuickLinks:
    login_page = f"{BASE_URL}/login"
    dashboard = f"{BASE_URL}/account/"
    rc_rosters = f"{BASE_URL}/account/?view=manage&tab=rcrosters"
    find_certificate = "https://www.redcross.org/take-a-class/digital-certificate"
    redcross = "https://www.redcrosslearningcenter.org/"

class EnrollNationwideLoginPage:
    username_input = (By.CSS_SELECTOR, "#email")
    password_input = (By.CSS_SELECTOR, "#password")
    remember_me_checkbox = (By.CSS_SELECTOR, "#remember_me")
    login_button = (By.XPATH, "//button[text()= 'Login']")
    info_needed_popup = (By.XPATH, "//a[text()= 'Fill out later']")

class EnrollNationwideRCRostersPage:
    roster_table = lambda x: (By.XPATH, f"//form[@id= 'cbp-certs']/div[2]/div/span{x}")
    all_classes = (By.XPATH, "//tbody/tr/td/a[1]")
    # one row per roster on the RC Rosters list; the parts below are looked up INSIDE a row
    roster_rows = (By.XPATH, "//tbody/tr")
    row_link = (By.XPATH, "./td/a[1]")
    row_missing = (By.XPATH, "./td[2]/div/span[contains(@class, 'is-missing')]")
    row_missing_text = (By.XPATH, "./td[2]/div/span[contains(@class, 'is-missing')]/small")
    row_passed = (By.XPATH, "./td[4]")
    row_finalized = (By.XPATH, "./td[3]/small")
    enrolled_std = (By.XPATH, "//li[text()= 'Enrolled']/strong")
    sku = (By.CSS_SELECTOR, ".cbp-sku-box > b")
    instructor_name = (By.XPATH, "//p[contains(text(), 'Instructor Name')]/child::strong")
    data_table_ = lambda x: (By.XPATH, f"//dt[normalize-space(text())= {_q(x)}]/following-sibling::dd[1]")
    # FIX: was a plain string (not a (By, value) tuple), so input_element could never find it
    cert_link_input = lambda x: (By.XPATH,
                                 f"//small[normalize-space(text())= {_q(x)}]/ancestor::span/following-sibling::span/following-sibling::span/child::input[@inputmode= 'url']")
    save_links_button = (By.XPATH, "//button[contains(text(), 'Save & email')]")
    # A class on the rosters list (absolute or relative href) - used to confirm it left the list after saving
    class_in_list = lambda abs_href, rel_href: (By.XPATH,
                                                f"//tbody/tr/td/a[@href= {_q(abs_href)} or @href= {_q(rel_href)}]")

class RedCrossLoginPage:
    login_button = (By.XPATH, "//a[@title= 'Login']")
    input_ = lambda x: (By.XPATH, f"//input[contains(@class, 'sfdc_{x}input')]")
    sign_in_button = (By.XPATH, "//button[contains(text(), 'LOGIN')]")
    nathan_profile = (By.XPATH, "//span[text()= 'Nathan Shell']")

class RedCrossClassListingPage:
    course_record_entry_btn = (By.XPATH, "//button[@title= 'Course Record Entry']")
    search_input = lambda x: (By.XPATH, f"(//div[@class='slds-show']/child::input)[{x}]")
    first_option = lambda x: (By.XPATH, f"//b[text()= {_q(x)}]/ancestor::span[@role='option']")
    course_input = (By.XPATH, "//button[@name= 'searchButton']/parent::div/preceding-sibling::div/input")
    course_search_button = (By.XPATH, "//button[@name= 'searchButton']")
    course_select_btn = (By.XPATH, "//button[@title= 'Select']")
    session_input = lambda x: (By.XPATH, f"//label[text()= {_q(x)}]/following-sibling::div//child::input")
    # FIX: was a plain string, not a (By, value) tuple. Index is 1-based (XPath).
    student_input = lambda x, y: (By.XPATH, f"(//label[text()= {_q(x)}]/following-sibling::div/input)[{y}]")
    evaluation_select = lambda x, y: (By.XPATH,
                                      f"(//span[contains(text(), {_q(x)})]/parent::label/following-sibling::div//select)[{y}]")
    time_zone_select = (By.ID, "zoneSelect")
    lookup_error = (By.CSS_SELECTOR, "div.lookupError")
    invoice_btn = (By.XPATH, "//a[text()= 'Invoice']")
    payment_btn = (By.XPATH, "//button[text()= 'Submit Payment']")
    legal_agreement_btn = (By.XPATH, "//span/input[@type= 'checkbox']")
    address_search = (By.XPATH, "//input[@placeholder='Address Search..']")
    address_option = lambda x: (By.XPATH, f"//span[contains(text(), {_q(x)})]/ancestor::lightning-base-combobox-item")
    # every dropdown option (outermost role=option only, so nested ones aren't counted twice)
    location_options = (By.XPATH, "//*[@role='option'][not(ancestor::*[@role='option'])]")
    address_first_option = (By.XPATH, "(//lightning-base-combobox-item)[1]")
    site_name_input = (By.XPATH, "//label[text()= 'Site Name']/following-sibling::div//input")
    submit_button = lambda x: (By.XPATH, f"//button[contains(text(), {_q(x)})]")
    # FIX: the old version used a backslash inside an f-string expression,
    # which is a SyntaxError on Python 3.11 and older.
    # tags: 'b' or ('b', 'span'); texts are OR-ed and matched case-insensitively
    option_match = lambda tags, texts: (
        By.XPATH,
        "//*[" + " or ".join(f"self::{t}" for t in ((tags,) if isinstance(tags, str) else tags)) + "]"
        "[ancestor::span[@role='option']]["
        + " or ".join(
            "contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
            f"{_q(str(t).lower())})" for t in texts
        )
        + "]/ancestor::span[@role='option'][1]",
    )
    verifying_emails_spinner = (By.CSS_SELECTOR, "span.progressMsg")
    proceed_button = (By.XPATH, "//button[text()= 'Proceed']")

class RCCertificatesPage:
    search_input = (By.ID, "dwfrm_certificate_email")
    search_button = (By.XPATH, "(//button[@type= 'submit' and @value= 'Find'])[1]")
    cert_id = (By.ID, "certid")