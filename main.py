import os
import logging
from datetime import date, datetime
from dotenv import load_dotenv
from utils import (
    logger, get_undetected_driver, is_driver_alive, close_extra_tabs,
    load_state, save_state, parse_class_date,
    record_failed_class, get_failed_classes, send_discord_message, remove_failed_class,
)
from enrollNationwideFunctions import (
    login_to_enrollNationwide, open_next_class, open_class_page, get_class_details,
    add_cert_links_to_students, class_left_rosters_list,
)
from redcrossFunctions import (
    login_on_redcross, add_class_info, add_students_info,
    accept_agreement_and_finalize, get_cert_links,
    NOT_SUBMITTED, SUBMITTED,
)

load_dotenv()

# Don't push classes that haven't happened yet to Red Cross
SKIP_FUTURE_CLASSES = True

# Stages saved in class_state.json
STAGE_SUBMITTING = "submitting"      # payment/submit clicked on Red Cross - NEVER resubmit
STAGE_FINISHED = "finished"          # links saved with 'Save & email' (the site then finishes the class)


def verify_environment_variables():
    required_vars = ["ENW_USERNAME", "ENW_PASSWORD", "RC_USERNAME", "RC_PASSWORD"]
    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        return False
    return True


def set_stage(state: dict, link: str, stage, **extra):
    entry = state.setdefault(link, {})
    if stage is None:
        entry.pop("stage", None)
    else:
        entry["stage"] = stage
    entry.update(extra)
    entry["updated"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)


def open_redcross_tab(driver) -> bool:
    driver.switch_to.new_window('tab')  # FIX: don't rely on window_handles[1] ordering
    return login_on_redcross(driver, os.getenv("RC_USERNAME"), os.getenv("RC_PASSWORD"))


def process_class(driver, link: str, state: dict) -> bool | None:
    """Process ONE class from start to finish:

      1. open the class page
      2. fetch the class details
      3. open Red Cross in a new tab, add the details and submit
      4. on that same tab, look up the certificate links, then close the tab
      5. back on the class page, paste the link for every student and click 'Save & email'
         (EnrollNationwide then marks the class finished and removes it from the rosters list)

    Returns True when the class is done, None when skipped, False when it failed.
    """
    main_handle = driver.current_window_handle
    entry = state.get(link, {})
    stage = entry.get("stage")

    try:
        # ---------- 1. open the class ----------
        logger.info("Step 1/5: opening the class page")
        if not open_class_page(driver, link, os.getenv("ENW_USERNAME"), os.getenv("ENW_PASSWORD")):
            return False

        if stage in (STAGE_SUBMITTING, "certs_saved", STAGE_FINISHED):
            # Already paid/submitted in an earlier run: never create it again.
            # (A class still on the list after an earlier save gets its links fetched and saved again.)
            logger.info("Steps 2-3 skipped: this class was already submitted to Red Cross in an earlier run")
            students = entry.get("students", [])
            sku = entry.get("sku", "?")
            logger.info("Opening Red Cross in a new tab to fetch the certificates")
            if not open_redcross_tab(driver):
                logger.error("Red Cross login failed")
                return False
        else:
            # ---------- 2. fetch the details ----------
            logger.info("Step 2/5: fetching class details")
            class_details = get_class_details(driver, link)
            if not class_details:
                logger.error(f"Failed to retrieve valid class details for: {link}")
                return False
            sku = class_details['sku']
            students = class_details['students_data']
            logger.info(f"Class SKU {sku}, Instructor: {class_details['instructor_name']}, "
                        f"Date: {class_details['class_date']}, Students: {len(students)}")

            class_day = parse_class_date(class_details['class_date'])
            if SKIP_FUTURE_CLASSES and class_day and class_day > date.today():
                logger.info(f"Class {sku} is on {class_day} (future) - skipping for now")
                return None   # skipped, not a failure

            # ---------- 3. Red Cross: new tab, add details, submit ----------
            logger.info("Step 3/5: opening Red Cross in a new tab and submitting the class")
            if not open_redcross_tab(driver):
                logger.error("Red Cross login failed")
                return False
            if not add_class_info(class_details, driver):
                logger.error(f"Failed to add class info for SKU: {sku} - stopping this class (nothing submitted)")
                return False
            if not add_students_info(driver, students):
                logger.error(f"Failed to add students info for SKU: {sku} - stopping this class (nothing submitted)")
                return False

            def mark(stage_name):
                set_stage(state, link, stage_name, sku=sku, students=students,
                          class_date=class_details['class_date'])

            result = accept_agreement_and_finalize(driver, mark_stage=mark)
            if result == NOT_SUBMITTED:
                logger.error(f"Failed to finalize class for SKU: {sku} - nothing was submitted")
                return False
            if result != SUBMITTED:
                logger.warning(f"Submission for SKU {sku} could not be confirmed; checking certificates to verify")

        # ---------- 4. same tab: certificates, then close the tab ----------
        logger.info("Step 4/5: fetching certificate links on the Red Cross tab")
        cert_links = get_cert_links(driver, students)
        close_extra_tabs(driver, main_handle)     # closes Red Cross, back on the class page
        logger.info("Red Cross tab closed - back on the class page")

        if not cert_links:
            logger.error(f"No certificates found for SKU {sku}. Class stays open; the next run will "
                         f"retry certificates only (it will NOT resubmit). If this persists, check Red Cross manually.")
            return False

        # 'Save & email' finishes the class and emails the students, so only save when EVERY
        # successful student has a certificate link - otherwise leave the class for the next run.
        expected = [s['email'] for s in students if s.get('email') and s.get('result') == 'Successful']
        missing = [e for e in expected if e not in cert_links]
        if missing:
            logger.error(f"No certificate found yet for: {', '.join(missing)}. Not saving (saving would finish "
                         f"the class); the next run will fetch the certificates again (it will NOT resubmit).")
            return False

        # ---------- 5. paste links on the class page and 'Save & email' ----------
        logger.info("Step 5/5: pasting certificate links for every student and clicking 'Save & email'")
        if not add_cert_links_to_students(driver, link, cert_links):
            logger.error(f"Failed to save certificate links in EnrollNationwide for SKU: {sku}")
            return False
        set_stage(state, link, STAGE_FINISHED)

        # Saving finishes the class automatically - confirm it left the rosters list
        if not class_left_rosters_list(driver, link):
            return False
        logger.info(f"Class {link} done")
        return True

    finally:
        # A failure mid-class must not leave the Red Cross tab open for the next class
        if is_driver_alive(driver):
            close_extra_tabs(driver, main_handle)


def handle_skipped_roster(state: dict, link: str, title: str, result: dict):
    """A roster failed the list checks. Missing data -> Discord message, but only once per roster
    (the list is checked every 30 minutes, so don't repeat the same alert)."""
    missing = result.get("missing")
    if not missing:
        return   # 0 passed / too old: just skipped (already logged)
    entry = state.setdefault(link, {})
    if entry.get("missing_notified") == missing:
        return
    send_discord_message(
        f"**Roster skipped - missing data**\n{title}\n{link}\n"
        f"Missing: {missing}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    entry["missing_notified"] = missing
    entry["updated"] = datetime.now().isoformat(timespec="seconds")
    save_state(state)


class ErrorCollector(logging.Handler):
    """Remembers the ERROR lines logged while one class is processed, for the Discord message."""
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.messages = []

    def emit(self, record):
        try:
            self.messages.append(record.getMessage())
        except Exception:
            pass


def handle_failed_class(link: str, errors: list):
    """Save the failed class to failed_classes.txt and tell Discord right away."""
    record_failed_class(link)
    reason = "\n".join(f"- {e[:300]}" for e in errors[-5:]) or "- (no error details logged)"
    send_discord_message(
        f"**Class processing failed**\n{link}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"Reason:\n{reason}"
    )


def send_failed_classes_summary():
    """At the end of the run, send every URL in failed_classes.txt to Discord."""
    failed = get_failed_classes()
    if not failed:
        logger.info("No failed classes to report")
        return
    logger.info(f"Sending {len(failed)} failed class(es) to Discord")
    lines = "\n".join(f"{i}. {url}" for i, url in enumerate(failed, start=1))
    send_discord_message(f"**Automation finished - {len(failed)} class(es) need manual processing:**\n{lines}")


def start_session():
    driver = get_undetected_driver()
    if driver is None:
        logger.error("Could not start Chrome")
        return None
    if not login_to_enrollNationwide(driver, os.getenv("ENW_USERNAME"), os.getenv("ENW_PASSWORD")):
        logger.error("EnrollNationwide login failed")
        try:
            driver.quit()
        except Exception:
            pass
        return None
    return driver


def main():
    if not verify_environment_variables():
        logger.error("Exiting due to missing environment variables.")
        return

    try:
        state = load_state()
    except Exception:
        logger.error("Exiting: fix or remove the corrupt state file first (see message above).")
        return

    driver = start_session()
    if driver is None:
        send_discord_message("**Automation could not start** - Chrome or the EnrollNationwide login failed. "
                             "Check automation.log.")
        send_failed_classes_summary()
        return

    summary = {"finished": 0, "failed": 0, "skipped": 0}
    try:
        # Work through the to-do list ONE class at a time:
        #   class list -> open first class -> steps 1-6 -> back on the class list -> next class.
        # The list is re-read after every class, so a finished class has dropped off and the
        # next one is simply the first class still on the list.
        attempted = set()   # classes already tried in this run (failed/skipped ones stay on the list)
        class_no = 0
        while True:
            # FIX: recover if Chrome crashed/closed during the previous class
            if not is_driver_alive(driver):
                logger.warning("Browser session lost - restarting Chrome")
                try:
                    driver.quit()
                except Exception:
                    pass
                driver = start_session()
                if driver is None:
                    logger.error("Could not restart the browser; stopping")
                    break

            logger.info("Going to the class list to open the next class")
            already_submitted = {l for l, e in state.items()
                                 if e.get("stage") in (STAGE_SUBMITTING, "certs_saved", STAGE_FINISHED)}
            status, link = open_next_class(
                driver, attempted, already_submitted,
                on_skip=lambda l, title, r: handle_skipped_roster(state, l, title, r))
            if status == "error":
                logger.error("Could not load the class list (RC Rosters page); stopping")
                break
            if status == "empty":
                logger.info("Class list is empty - all done")
                break
            if status == "none_left":
                logger.info("No more rosters to process (the rest were skipped or failed earlier in this run) - stopping")
                break

            attempted.add(link)
            class_no += 1
            logger.info(f"===== Class {class_no}: {link} =====")

            collector = ErrorCollector()
            logger.addHandler(collector)
            try:
                result = process_class(driver, link, state)
            except Exception as e:
                # One bad class doesn't stop the rest
                logger.exception(f"Unexpected error on {link}: {e}")
                result = False
            finally:
                logger.removeHandler(collector)

            if result is True:
                summary["finished"] += 1
                remove_failed_class(link)   # it may have failed in an earlier run and been fixed since
            elif result is None:
                summary["skipped"] += 1
            else:
                summary["failed"] += 1
                handle_failed_class(link, collector.messages)

    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
    finally:
        logger.info(f"Run summary: {summary['finished']} finished, {summary['failed']} failed, "
                    f"{summary['skipped']} skipped (future date)")
        try:
            send_failed_classes_summary()
        except Exception as e:
            logger.error(f"Could not send failed-classes summary: {e}")
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        logger.info("Driver closed. Exiting the script.")


if __name__ == "__main__":
    main()