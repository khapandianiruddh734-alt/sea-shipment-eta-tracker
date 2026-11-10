
import re
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ==================================================
# CONFIGURATION
# ==================================================

TRACK_TRACE_URL = "https://www.track-trace.com/bol"


# ==================================================
# NORMALIZE MBL
# ==================================================

def normalize_mbl(value):

    return re.sub(
        r"[\s\-]+",
        "",
        str(value).strip().upper()
    )


# ==================================================
# ETA EXTRACTION
# ==================================================


def extract_eta(page_text):

    # ==========================================
    # 1. MAERSK
    # ==========================================

    # Example: 24 Nov 2026 (ETA)
    # Ignore the previous ETA shown after "was".

    maersk_pattern = (
        r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})"
        r"\s*\(ETA\)"
    )

    match = re.search(
        maersk_pattern,
        page_text,
        re.IGNORECASE
    )

    if match:
        try:
            eta = datetime.strptime(
                match.group(1).strip(),
                "%d %b %Y"
            )

            return eta.strftime("%Y-%m-%d")

        except ValueError:
            pass

    # ==========================================
    # 2. EVERGREEN
    # ==========================================

    evergreen_patterns = [
        r"Estimated Date of Arrival at Destination\s*:\s*([A-Z]{3}-\d{2}-\d{4})",
        r"Estimated Arrival Date\s*:?\s*([A-Z]{3}-\d{2}-\d{4})",
    ]

    for pattern in evergreen_patterns:

        match = re.search(
            pattern,
            page_text,
            re.IGNORECASE
        )

        if match:
            try:
                eta = datetime.strptime(
                    match.group(1).upper(),
                    "%b-%d-%Y"
                )

                return eta.strftime("%Y-%m-%d")

            except ValueError:
                pass

    # ==========================================
    # 3. ONE - SPECIFIC ARRIVAL EVENT
    # ==========================================

    one_pattern = (
        r"Vessel Arrival at Port of Discharge"
        r"\s+(\d{4}-\d{2}-\d{2})"
    )

    match = re.search(
        one_pattern,
        page_text,
        re.IGNORECASE
    )

    if match:
        try:
            eta = datetime.strptime(
                match.group(1),
                "%Y-%m-%d"
            )

            return eta.strftime("%Y-%m-%d")

        except ValueError:
            pass

    # ==========================================
    # 4. ONE - POD / VESSEL ARRIVAL FALLBACK
    # ==========================================

    one_fallback = (
        r"POD\s*/\s*Vessel Arrival"
        r".{0,1500}?"
        r"([A-Z][A-Z\s,.-]+,\s*(?:INDIA|THAILAND|UAE|SINGAPORE))"
        r"\s+(\d{4}-\d{2}-\d{2})"
    )

    match = re.search(
        one_fallback,
        page_text,
        re.IGNORECASE | re.DOTALL
    )

    if match:
        try:
            eta = datetime.strptime(
                match.group(2),
                "%Y-%m-%d"
            )

            return eta.strftime("%Y-%m-%d")

        except ValueError:
            pass

    return None



# ==================================================
# SHIPPING LINE IDENTIFICATION
# ==================================================

def identify_shipping_line(url, page_title, page_text):

    content = (
        url + " " +
        page_title + " " +
        page_text[:5000]
    ).lower()

    carriers = {

        "Evergreen": [
            "shipmentlink",
            "evergreen"
        ],

        "ONE": [
            "ocean network express",
            "one-line",
            "one cookie preferences"
        ],

        "Maersk": [
            "maersk"
        ],

        "CMA CGM": [
            "cma cgm"
        ],

        "Hapag-Lloyd": [
            "hapag-lloyd",
            "hapag lloyd"
        ],

        "COSCO": [
            "cosco"
        ],

        "MSC": [
            "mediterranean shipping",
            "msc"
        ],

        "HMM": [
            "hmm"
        ],

        "Yang Ming": [
            "yang ming"
        ],

        "ZIM": [
            "zim"
        ],

        "Wan Hai": [
            "wan hai"
        ],

        "PIL": [
            "pacific international lines"
        ],
    }

    for carrier, keywords in carriers.items():

        if any(keyword in content for keyword in keywords):

            return carrier

    return "Unknown"


# ==================================================
# TRACK ONE MBL
# ==================================================

def track_mbl(context, mbl):

    mbl = normalize_mbl(mbl)

    if not mbl:
        return None

    print("\n" + "=" * 60)
    print("TRACKING MBL:", mbl)
    print("=" * 60)

    page = context.new_page()
    result_page = None

    try:

        # ------------------------------------------
        # OPEN TRACK-TRACE
        # ------------------------------------------

        print("\nOpening Track-Trace...")

        page.goto(
            TRACK_TRACE_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.locator(
            'input[name="number"]'
        ).fill(mbl)

        print("MBL entered:", mbl)

        # ------------------------------------------
        # OPEN CARRIER TRACKING PAGE
        # ------------------------------------------

        print("Opening shipping-line tracking page...")

        with context.expect_page(timeout=30000) as new_page_info:

            page.locator(
                "#wc-multi-form-button_direct"
            ).click()

        result_page = new_page_info.value

        result_page.wait_for_load_state(
            "domcontentloaded",
            timeout=60000
        )

        print("\nTRACKING PAGE OPENED")
        print("URL:", result_page.url)
        print("TITLE:", result_page.title())

        # ------------------------------------------
        # CAPTCHA INSTRUCTIONS
        # ------------------------------------------

        print("\nCAPTCHA INSTRUCTIONS")

        print(
            "If CAPTCHA or verification appears, "
            "solve it manually in the browser."
        )

        print(
            "Wait until the tracking result is fully displayed."
        )

        input(
            "\nPress ENTER in terminal after tracking result appears..."
        )

        # ------------------------------------------
        # READ TRACKING RESULT
        # ------------------------------------------

        page_text = result_page.locator(
            "body"
        ).inner_text()

        carrier = identify_shipping_line(
            result_page.url,
            result_page.title(),
            page_text
        )

        eta = extract_eta(page_text)

        # ------------------------------------------
        # DISPLAY RESULT
        # ------------------------------------------

        print("\n--- TRACKING RESULT ---")

        print("MBL:", mbl)
        print("Shipping Line:", carrier)

        if eta:

            print("Extracted ETA:", eta)
            print("Status: SUCCESS")

            status = "SUCCESS"

        else:

            print("Extracted ETA: NOT FOUND")
            print("Status: MANUAL REVIEW REQUIRED")

            print("\nTracking page text:")
            print(page_text[:5000])

            status = "ETA_NOT_FOUND"

        return {
            "mbl": mbl,
            "carrier": carrier,
            "eta": eta,
            "tracking_url": result_page.url,
            "status": status
        }

    except PlaywrightTimeoutError:

        print("\nTIMEOUT: Tracking page did not open in time.")

        return {
            "mbl": mbl,
            "carrier": "Unknown",
            "eta": None,
            "tracking_url": "",
            "status": "TIMEOUT"
        }

    except Exception as error:

        print("\nTRACKING ERROR:", str(error))

        return {
            "mbl": mbl,
            "carrier": "Unknown",
            "eta": None,
            "tracking_url": "",
            "status": "ERROR"
        }

    finally:

        # Close this MBL's tabs before processing next MBL
        if result_page and not result_page.is_closed():

            result_page.close()

        if not page.is_closed():

            page.close()


# ==================================================
# MAIN PROGRAM
# ==================================================

def main():

    print("\n")
    print("=" * 60)
    print("SEA SHIPMENT MULTI-CARRIER ETA TRACKER")
    print("=" * 60)

    print("\nEnter MBL numbers one by one.")
    print("Press ENTER on an empty line when finished.\n")

    mbl_list = []

    while True:

        mbl = input("Enter MBL: ").strip()

        if not mbl:
            break

        mbl = normalize_mbl(mbl)

        if mbl in mbl_list:

            print("Duplicate MBL skipped.")

        else:

            mbl_list.append(mbl)

    if not mbl_list:

        print("\nNo MBL numbers entered.")
        return

    results = []

    # ----------------------------------------------
    # START BROWSER
    # ----------------------------------------------

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=False
        )

        context = browser.new_context()

        try:

            for mbl in mbl_list:

                result = track_mbl(
                    context,
                    mbl
                )

                if result:

                    results.append(result)

        finally:

            browser.close()

    # ----------------------------------------------
    # FINAL SUMMARY
    # ----------------------------------------------

    print("\n")
    print("=" * 75)
    print("FINAL TRACKING SUMMARY")
    print("=" * 75)

    for result in results:

        print(
            result["mbl"],
            "|",
            result["carrier"],
            "|",
            result["eta"] or "ETA NOT FOUND",
            "|",
            result["status"]
        )

    print("\nTotal MBLs:", len(results))

    successful = sum(
        1 for result in results
        if result["status"] == "SUCCESS"
    )

    print("Successful ETAs:", successful)

    print("Unsuccessful:", len(results) - successful)

    print("\nTracking session completed.")


# ==================================================
# RUN PROGRAM
# ==================================================

if __name__ == "__main__":

    main()
