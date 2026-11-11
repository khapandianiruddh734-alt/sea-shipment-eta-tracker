import os
import re
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from supabase import create_client

# ==================================================
# CONFIGURATION
# ==================================================
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
TRACK_TRACE_URL = "https://www.track-trace.com/bol"
POLL_SECONDS = 5

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

# ==================================================
# COMMON FUNCTIONS
# ==================================================
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_mbl(value):
    return re.sub(r"[\s\-]+", "", str(value or "").strip().upper())


def update_worker_status(status, current_mbl=None, message="", cycle_started=None, cycle_finished=None):
    row = {"id": 1, "status": status, "current_mbl": current_mbl,
           "last_heartbeat": now_iso(), "message": (message or "")[:1000], "updated_at": now_iso()}
    if cycle_started is not None:
        row["last_cycle_started"] = cycle_started
    if cycle_finished is not None:
        row["last_cycle_finished"] = cycle_finished
    sb.table("tracking_worker_status").upsert(row, on_conflict="id").execute()

# ==================================================
# ETA EXTRACTION
# ==================================================
def extract_eta(page_text):
    patterns = [
        (r"(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s*\(ETA\)", "%d %b %Y"),
        (r"Estimated Date of Arrival at Destination\s*:\s*([A-Z]{3}-\d{2}-\d{4})", "%b-%d-%Y"),
        (r"Estimated Arrival Date\s*:?\s*([A-Z]{3}-\d{2}-\d{4})", "%b-%d-%Y"),
        (r"Vessel Arrival at Port of Discharge\s+(\d{4}-\d{2}-\d{2})", "%Y-%m-%d"),
    ]
    for pattern, date_format in patterns:
        match = re.search(pattern, page_text, re.IGNORECASE)
        if match:
            try:
                return datetime.strptime(match.group(1).strip(), date_format).date().isoformat()
            except ValueError:
                pass
    fallback = (r"POD\s*/\s*Vessel Arrival.{0,1500}?"
                r"[A-Z][A-Z\s,.-]+,\s*(?:INDIA|THAILAND|UAE|SINGAPORE)\s+(\d{4}-\d{2}-\d{2})")
    match = re.search(fallback, page_text, re.IGNORECASE | re.DOTALL)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    return None

# ==================================================
# SHIPPING LINE IDENTIFICATION
# ==================================================
def identify_carrier(url, title, page_text):
    content = f"{url} {title} {page_text[:5000]}".lower()
    carriers = {
        "Evergreen": ("shipmentlink", "evergreen"), "ONE": ("ocean network express", "one-line"),
        "Maersk": ("maersk",), "CMA CGM": ("cma cgm",),
        "Hapag-Lloyd": ("hapag-lloyd", "hapag lloyd"), "COSCO": ("cosco",),
        "MSC": ("mediterranean shipping", "msc"), "HMM": ("hmm",),
        "Yang Ming": ("yang ming",), "ZIM": ("zim",), "Wan Hai": ("wan hai",),
        "PIL": ("pacific international lines",),
    }
    for carrier, keywords in carriers.items():
        if any(keyword in content for keyword in keywords):
            return carrier
    return "Unknown"

# ==================================================
# SUPABASE SHIPMENT UPDATE
# ==================================================
def update_shipment(ref, carrier, eta=None, status="ERROR", error=""):
    existing = (sb.table("current_shipments").select("latest_eta,carrier,is_active")
                .eq("reference_number", ref).limit(1).execute().data or [])
    old_eta = (existing[0].get("latest_eta") or "") if existing else ""
    old_carrier = (existing[0].get("carrier") or "") if existing else ""
    now = now_iso()
    if eta and eta != old_eta:
        sb.table("eta_history").insert({"reference_number": ref, "old_eta": old_eta,
            "new_eta": eta, "changed_at": now,
            "source": f"Playwright / Track-Trace ({carrier})"}).execute()
    row = {"reference_number": ref, "reference_type": "MBL",
           "carrier": carrier if carrier != "Unknown" else old_carrier,
           "last_checked": now, "tracking_result": status,
           "tracking_error": (error or "")[:1000], "is_active": True}
    if eta:
        row.update({"latest_eta": eta, "previous_eta": old_eta if old_eta and old_eta != eta else None,
                    "eta_is_actual": 0, "eta_changed_at": now if old_eta != eta else None})
    sb.table("current_shipments").upsert(row, on_conflict="reference_number").execute()

# ==================================================
# TRACKING SCHEDULE / SHIPMENT QUERIES
# ==================================================
def get_interval_seconds():
    result = sb.table("tracking_settings").select("interval_minutes").eq("id", 1).single().execute()
    minutes = int(result.data.get("interval_minutes", 360))
    if minutes not in (30, 360, 720):
        minutes = 360
    return minutes * 60


def get_shipments():
    response = (sb.table("current_shipments").select("*").eq("is_active", True)
                .order("reference_number").execute())
    return response.data or []


def get_today_shipments():
    """Return active shipments added today according to India time."""
    india_tz = ZoneInfo("Asia/Kolkata")
    today = datetime.now(india_tz).date()
    start_ist = datetime.combine(today, datetime.min.time()).replace(tzinfo=india_tz)
    end_ist = start_ist + timedelta(days=1)
    start_utc = start_ist.astimezone(timezone.utc).isoformat()
    end_utc = end_ist.astimezone(timezone.utc).isoformat()
    response = (sb.table("current_shipments").select("*").eq("is_active", True)
                .gte("created_at", start_utc).lt("created_at", end_utc)
                .order("reference_number").execute())
    return response.data or []


def get_one_shipment(reference_number):
    ref = normalize_mbl(reference_number)
    if not ref:
        return []
    response = (sb.table("current_shipments").select("*").eq("is_active", True)
                .eq("reference_number", ref).limit(1).execute())
    return response.data or []


def get_prefix_shipments(prefix):
    prefix = normalize_mbl(prefix)
    if not prefix:
        return []
    response = (sb.table("current_shipments").select("*").eq("is_active", True)
                .ilike("reference_number", f"{prefix}%")
                .order("reference_number").execute())
    return response.data or []


def get_selected_shipments(references):
    refs = list(dict.fromkeys(normalize_mbl(x) for x in (references or []) if normalize_mbl(x)))
    if not refs:
        return []
    response = (sb.table("current_shipments").select("*").eq("is_active", True)
                .in_("reference_number", refs).order("reference_number").execute())
    return response.data or []

# ==================================================
# TRACK ONE MBL
# ==================================================

def track_one(context, ref):
    page = context.new_page()
    result_page = None

    try:
        print(f"\nOpening Track-Trace for {ref}")
        update_worker_status("RUNNING", current_mbl=ref, message=f"Tracking {ref}")

        page.goto(TRACK_TRACE_URL, wait_until="domcontentloaded", timeout=60000)
        page.locator('input[name="number"]').fill(ref)

        with context.expect_page(timeout=30000) as opened:
            page.locator("#wc-multi-form-button_direct").click()

        result_page = opened.value
        result_page.wait_for_load_state("domcontentloaded", timeout=60000)

        print("Tracking page:", result_page.url)
        print("Page title:", result_page.title())
        print("Waiting for tracking information...")

        result_page.locator("body").wait_for(state="visible", timeout=60000)
        result_page.wait_for_timeout(10000)

        page_text = result_page.locator("body").inner_text()

        # Identify carrier BEFORE checking whether it is Maersk
        carrier = identify_carrier(
            result_page.url,
            result_page.title(),
            page_text
        )

        # Temporary Maersk debugging
        if carrier == "Maersk":
            print("\n========== MAERSK PAGE CONTENT ==========")
            print(page_text[:12000])
            print("========== END MAERSK CONTENT ==========\n")

        if not page_text.strip():
            raise Exception("Tracking page returned empty content.")

        eta = extract_eta(page_text)

        if eta:
            update_shipment(ref, carrier, eta, "SUCCESS")
            print(f"SUCCESS | {ref} | {carrier} | ETA: {eta}")
        else:
            update_shipment(
                ref,
                carrier,
                None,
                "ETA_NOT_FOUND",
                "Tracking page opened, but supported ETA patterns did not find a date."
            )
            print(f"ETA NOT FOUND | {ref} | {carrier}")
            print("Manual review required. No ETA was guessed.")

    except PlaywrightTimeoutError as exc:
        update_shipment(ref, "Unknown", status="TIMEOUT", error=str(exc))
        print(f"TIMEOUT | {ref} | {exc}")

    except Exception as exc:
        update_shipment(ref, "Unknown", status="ERROR", error=str(exc))
        print(f"ERROR | {ref} | {exc}")

    finally:
        if result_page and not result_page.is_closed():
            result_page.close()

        if not page.is_closed():
            page.close()

# ==================================================
# TRACKING CYCLE
# ==================================================
def run_cycle(shipments=None, label="automatic tracking"):
    if shipments is None:
        shipments = get_shipments()
    if not shipments:
        print(f"No active shipments found for {label}.")
        update_worker_status("WAITING", current_mbl=None, message=f"No shipments found for {label}.")
        return 0
    print(f"\nFound {len(shipments)} shipment(s) for {label}.")
    started = now_iso()
    update_worker_status("RUNNING", message=f"{label}: started for {len(shipments)} shipment(s).", cycle_started=started)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        try:
            for index, shipment in enumerate(shipments, 1):
                ref = normalize_mbl(shipment.get("reference_number"))
                if not ref:
                    continue
                print(f"\n[{index}/{len(shipments)}] Tracking {ref}")
                track_one(context, ref)
                time.sleep(2)
        finally:
            context.close()
            browser.close()
    finished = now_iso()
    update_worker_status("WAITING", current_mbl=None,
        message=f"{label}: attempted {len(shipments)} shipment(s). Check each result for success/errors.",
        cycle_finished=finished)
    print(f"\nTracking cycle completed for {len(shipments)} shipment(s).")
    return len(shipments)

# ==================================================
# DASHBOARD COMMAND QUEUE
# ==================================================
def get_pending_command():
    result = (sb.table("tracking_commands").select("*").eq("status", "PENDING")
              .order("created_at").limit(1).execute())
    return result.data[0] if result.data else None


def update_command(command_id, status, message=""):
    sb.table("tracking_commands").update({"status": status, "processed_at": now_iso(),
        "message": (message or "")[:1000]}).eq("id", command_id).execute()


def process_command(command):
    command_id = command["id"]
    action = str(command.get("action") or "").strip().upper()
    sb.table("tracking_commands").update({"status": "PROCESSING", "message": "Worker received command."})\
        .eq("id", command_id).execute()
    try:
        if action in ("START", "STOP"):
            update_command(command_id, "COMPLETED", f"{action} command accepted.")
            print(f"\nDashboard command received: {action}")
            return {"action": action}
        if action == "TRACK_ONE":
            shipments = get_one_shipment(command.get("reference_number"))
            label = f"manual tracking for {command.get('reference_number', '')}"
        elif action == "TRACK_SELECTED":
            shipments = get_selected_shipments(command.get("reference_numbers") or [])
            label = "selected MBL tracking"
        elif action == "TRACK_PREFIX":
            shipments = get_prefix_shipments(command.get("reference_prefix") or "")
            label = f"prefix tracking ({command.get('reference_prefix', '')})"
        elif action == "TRACK_TODAY":
            shipments = get_today_shipments()
            label = "today's shipments tracking (India time)"
        elif action == "TRACK_ALL":
            shipments = get_shipments()
            label = "all active shipments tracking"
        else:
            update_command(command_id, "FAILED", f"Unknown command: {action}")
            return None
        if not shipments:
            update_command(command_id, "COMPLETED", f"No active shipments matched {label}.")
            update_worker_status("WAITING", current_mbl=None, message=f"No active shipments matched {label}.")
            return None
        return {"action": action, "command_id": command_id, "shipments": shipments, "label": label}
    except Exception as exc:
        update_command(command_id, "FAILED", str(exc))
        print(f"Command failed ({action}): {exc}")
        return None

# ==================================================
# MAIN WORKER LOOP
# ==================================================
def main():
    active = False
    next_cycle_time = None
    print("=" * 60)
    print("SEA SHIPMENT ETA TRACKING WORKER")
    print("=" * 60)
    print("Waiting for dashboard commands. Keep this computer and terminal running.")
    update_worker_status("STOPPED", message="Worker connected. Waiting for dashboard command.")
    while True:
        try:
            command = get_pending_command()
            if command:
                result = process_command(command)
                if result:
                    action = result["action"]
                    if action == "START":
                        active = True
                        next_cycle_time = time.time()
                        update_worker_status("WAITING", message="Automatic tracking enabled.")
                    elif action == "STOP":
                        active = False
                        next_cycle_time = None
                        update_worker_status("STOPPED", message="Automatic tracking stopped by dashboard command.")
                    elif action in ("TRACK_ONE", "TRACK_SELECTED", "TRACK_PREFIX", "TRACK_TODAY", "TRACK_ALL"):
                        command_id = result["command_id"]
                        try:
                            count = run_cycle(result["shipments"], result["label"])
                            update_command(command_id, "COMPLETED", f"Tracking attempt finished for {count} shipment(s). Review shipment statuses for individual results.")
                        except Exception as exc:
                            update_command(command_id, "FAILED", str(exc))
                            update_worker_status("ERROR", message=f"Manual tracking failed: {exc}")
                            print(f"Manual tracking error: {exc}")
                        if active:
                            update_worker_status("WAITING", message="Manual request finished. Automatic tracking remains enabled.")
                        else:
                            update_worker_status("STOPPED", message="Manual tracking request finished. Automatic tracking is stopped.")
            if active and next_cycle_time is not None and time.time() >= next_cycle_time:
                try:
                    run_cycle()
                except Exception as exc:
                    print(f"Tracking cycle error: {exc}")
                    update_worker_status("ERROR", message=str(exc))
                interval = get_interval_seconds()
                next_cycle_time = time.time() + interval
                if active:
                    update_worker_status("WAITING", message=f"Next automatic cycle in {interval // 60} minutes.")
                    print(f"\nNext tracking cycle in {interval // 60} minutes.")
            elif not active:
                # Do not overwrite a recent manual cycle message with an idle status every poll.
                pass
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print("\nWorker stopped from keyboard.")
            update_worker_status("STOPPED", message="Worker stopped manually from Windows.")
            break
        except Exception as exc:
            print(f"\nWorker error: {exc}")
            try:
                update_worker_status("ERROR", message=str(exc))
            except Exception:
                pass
            print("Retrying in 10 seconds.")
            time.sleep(10)


if __name__ == "__main__":
    main()
