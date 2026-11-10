
from playwright.sync_api import sync_playwright, TimeoutError
import re
from datetime import datetime

# Enter your MBL number here
MBL = "EGLV050650261124"


with sync_playwright() as p:

    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    try:
        print("\nOpening Track-Trace...")

        page.goto(
            "https://www.track-trace.com/bol",
            wait_until="domcontentloaded",
            timeout=60000
        )

        # Enter MBL
        page.locator('input[name="number"]').fill(MBL)

        print("MBL entered:", MBL)
        print("Clicking Track direct...")

        # Capture the new tracking tab
        with context.expect_page(timeout=30000) as new_page_info:
            page.locator("#wc-multi-form-button_direct").click()

        result_page = new_page_info.value

        result_page.wait_for_load_state(
            "domcontentloaded",
            timeout=60000
        )

        print("\nNEW TAB OPENED")
        print("URL:", result_page.url)
        print("TITLE:", result_page.title())

        # Allow manual CAPTCHA completion
        input(
            "\nIf CAPTCHA appears, solve it manually. "
            "Wait for the tracking result, then press ENTER..."
        )

        # Read tracking page
        print("\n--- TRACKING RESULT ---")

        result_text = result_page.locator("body").inner_text()

        print(result_text[:12000])

        # Extract ETA
        patterns = [
            r"Estimated Date of Arrival at Destination\s*:\s*([A-Z]{3}-\d{2}-\d{4})",
            r"Estimated Arrival Date\s+([A-Z]{3}-\d{2}-\d{4})"
        ]

        eta_date = None

        for pattern in patterns:
            match = re.search(
                pattern,
                result_text,
                re.IGNORECASE
            )

            if match:
                eta_text = match.group(1).upper()

                try:
                    eta_date = datetime.strptime(
                        eta_text,
                        "%b-%d-%Y"
                    )
                    break
                except ValueError:
                    continue

        # Display extracted ETA
        print("\n--- AUTOMATIC ETA EXTRACTION ---")

        if eta_date:
            print("MBL:", MBL)
            print("Shipping Line: Evergreen")
            print("Extracted ETA:", eta_date.strftime("%Y-%m-%d"))
            print("ETA extraction successful!")

        else:
            print("ETA NOT FOUND.")
            print("Please check the tracking result manually.")

    except TimeoutError:
        print("\nTIMEOUT: Tracking page did not open or load in time.")

    except Exception as e:
        print("\nERROR:", str(e))

    finally:
        input("\nPress ENTER to close browser...")
        browser.close()
