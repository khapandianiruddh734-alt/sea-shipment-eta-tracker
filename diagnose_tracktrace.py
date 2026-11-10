from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()

    page.on("console", lambda msg: print("CONSOLE:", msg.type, msg.text))
    page.on("pageerror", lambda error: print("PAGE ERROR:", error))
    page.on("requestfailed", lambda request: print(
        "REQUEST FAILED:", request.url, request.failure
    ))

    page.goto("https://www.track-trace.com/bol")
    page.wait_for_load_state("domcontentloaded")

    print("\n--- INPUT FIELDS ---")
    for item in page.locator("input").all():
        print(item.evaluate(
            "(e) => ({type:e.type, name:e.name, id:e.id, placeholder:e.placeholder, value:e.value})"
        ))

    print("\n--- BUTTONS AND LINKS ---")
    for item in page.locator("button, input[type=submit], a").all():
        info = item.evaluate(
            "(e) => ({tag:e.tagName, text:e.innerText, value:e.value, href:e.href, type:e.type})"
        )
        if info.get("text") or info.get("value"):
            print(info)

    print("\n--- FORMS ---")
    for item in page.locator("form").all():
        print(item.evaluate(
            "(e) => ({action:e.action, method:e.method, html:e.outerHTML.slice(0,4000)})"
        ))

    input("\nPress ENTER to close browser...")
    browser.close()
