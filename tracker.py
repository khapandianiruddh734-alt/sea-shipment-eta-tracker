    import os
    import sqlite3
    import time
    from datetime import datetime, timezone
    from pathlib import Path

    import pandas as pd
    import requests
    from dotenv import load_dotenv

    load_dotenv()

    API_URL = "https://api.tracking.one/v1/shipments/ocean"
    API_KEY = os.getenv("TRACKING_ONE_API_KEY", "").strip()
    INPUT_FILE = Path("shipments.xlsx")
    OUTPUT_FILE = Path("shipment_tracking_output.xlsx")
    DB_FILE = Path("eta_tracking.db")
    REQUEST_TIMEOUT = 45


    def now_utc():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


    def db_connect():
        con = sqlite3.connect(DB_FILE)
        con.execute("""
            CREATE TABLE IF NOT EXISTS current_shipments (
                reference_number TEXT PRIMARY KEY,
                reference_type TEXT,
                carrier TEXT,
                vessel TEXT,
                voyage TEXT,
                pol TEXT,
                pod TEXT,
                baseline_eta TEXT,
                latest_eta TEXT,
                eta_is_actual INTEGER,
                shipment_status TEXT,
                previous_eta TEXT,
                eta_changed_at TEXT,
                last_checked TEXT,
                provider_updated_at TEXT,
                tracking_result TEXT,
                tracking_error TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS eta_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reference_number TEXT NOT NULL,
                old_eta TEXT,
                new_eta TEXT,
                changed_at TEXT NOT NULL,
                source TEXT
            )
        """)
        con.commit()
        return con


    def clean(value):
        if pd.isna(value):
            return ""
        return str(value).strip()


    def get_location_name(obj):
        return ((obj or {}).get("location") or {}).get("name", "") or ""


    def fetch_tracking(row):
        if not API_KEY:
            raise RuntimeError("API key missing. Add TRACKING_ONE_API_KEY to .env")

        params = {
            "referenceNumber": row["ReferenceNumber"],
            "referenceType": row["ReferenceType"] or "BL",
            "routeDetails": "false",
        }
        if row["CarrierCode"]:
            params["carrierCode"] = row["CarrierCode"]

        headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/json"}
        response = None
        for attempt in range(3):
            response = requests.get(
                API_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            )
            if response.status_code != 202:
                break
            if attempt < 2:
                print(f"{row['ReferenceNumber']}: provider is processing the lookup; retrying in 90 seconds...")
                time.sleep(90)
        if response is None:
            raise RuntimeError("No response received from tracking provider.")
        if response.status_code == 202:
            raise RuntimeError("Provider accepted the lookup but it is still processing (HTTP 202). Will retry on the next scheduled run.")
        if response.status_code == 401:
            raise RuntimeError("API authentication failed (HTTP 401). Check the API key.")
        if response.status_code == 402:
            raise RuntimeError("Provider usage limit or credits exhausted (HTTP 402). Check your plan.")
        if response.status_code == 429:
            wait = response.headers.get("Retry-After", "unknown")
            raise RuntimeError(f"Rate limit reached (HTTP 429). Retry-After: {wait}")
        response.raise_for_status()

        payload = response.json()
        if not payload.get("success") or not payload.get("shipment"):
            raise RuntimeError(f"Provider returned status: {payload.get('status', 'unknown')}")

        shipment = payload["shipment"]
        route = shipment.get("route") or {}
        pod = route.get("portOfDischarge") or {}
        pol = route.get("portOfLoading") or {}
        arrival = pod.get("arrival") or {}
        departure = pol.get("departure") or {}
        transport = arrival.get("transport") or {}
        if not transport:
            transport = departure.get("transport") or {}

        return {
            "carrier": (shipment.get("carrier") or {}).get("name", row["CarrierName"]),
            "vessel": transport.get("name", ""),
            "voyage": transport.get("voyageNumber", ""),
            "pol": get_location_name(pol),
            "pod": get_location_name(pod),
            "latest_eta": arrival.get("date", ""),
            "eta_is_actual": bool(arrival.get("isActual", False)),
            "shipment_status": shipment.get("status", ""),
            "provider_updated_at": shipment.get("updatedAt", ""),
            "tracking_result": payload.get("status", "OK"),
        }


    def save_success(con, row, data):
        ref = row["ReferenceNumber"]
        old = con.execute(
            "SELECT latest_eta FROM current_shipments WHERE reference_number=?",
            (ref,)
        ).fetchone()
        old_eta = old[0] if old else ""
        new_eta = data["latest_eta"]
        changed_at = now_utc() if old_eta and new_eta and old_eta != new_eta else ""

        if changed_at:
            con.execute(
                """INSERT INTO eta_history
                (reference_number, old_eta, new_eta, changed_at, source)
                VALUES (?, ?, ?, ?, ?)""",
                (ref, old_eta, new_eta, changed_at, "Tracking One API")
            )

        con.execute("""
            INSERT INTO current_shipments (
                reference_number, reference_type, carrier, vessel, voyage, pol, pod,
                baseline_eta, latest_eta, eta_is_actual, shipment_status, previous_eta,
                eta_changed_at, last_checked, provider_updated_at, tracking_result, tracking_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')
            ON CONFLICT(reference_number) DO UPDATE SET
                reference_type=excluded.reference_type,
                carrier=excluded.carrier,
                vessel=excluded.vessel,
                voyage=excluded.voyage,
                pol=COALESCE(NULLIF(excluded.pol,''), current_shipments.pol),
                pod=COALESCE(NULLIF(excluded.pod,''), current_shipments.pod),
                baseline_eta=COALESCE(NULLIF(excluded.baseline_eta,''), current_shipments.baseline_eta),
                previous_eta=CASE WHEN excluded.latest_eta != '' AND excluded.latest_eta != current_shipments.latest_eta
                                THEN current_shipments.latest_eta ELSE current_shipments.previous_eta END,
                latest_eta=COALESCE(NULLIF(excluded.latest_eta,''), current_shipments.latest_eta),
                eta_is_actual=excluded.eta_is_actual,
                shipment_status=excluded.shipment_status,
                eta_changed_at=CASE WHEN excluded.eta_changed_at != '' THEN excluded.eta_changed_at
                                    ELSE current_shipments.eta_changed_at END,
                last_checked=excluded.last_checked,
                provider_updated_at=excluded.provider_updated_at,
                tracking_result=excluded.tracking_result,
                tracking_error=''
        """, (
            ref, row["ReferenceType"], data["carrier"], data["vessel"], data["voyage"],
            data["pol"], data["pod"], row["BaselineETA"], new_eta,
            int(data["eta_is_actual"]), data["shipment_status"], old_eta,
            changed_at, now_utc(), data["provider_updated_at"], data["tracking_result"]
        ))
        con.commit()


    def save_error(con, ref, message):
        con.execute("""
            UPDATE current_shipments SET last_checked=?, tracking_error=?
            WHERE reference_number=?
        """, (now_utc(), message[:1000], ref))
        # Preserve previous ETA and shipment status when a lookup fails.
        if con.total_changes == 0:
            con.execute("""
                INSERT OR IGNORE INTO current_shipments
                (reference_number, last_checked, tracking_error)
                VALUES (?, ?, ?)
            """, (ref, now_utc(), message[:1000]))
        con.commit()


    def export_excel(con):
        df = pd.read_sql_query("SELECT * FROM current_shipments ORDER BY pod, latest_eta", con)
        history = pd.read_sql_query(
            "SELECT reference_number, old_eta, new_eta, changed_at, source "
            "FROM eta_history ORDER BY changed_at DESC", con
        )
        with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Live_Tracking", index=False)
            history.to_excel(writer, sheet_name="ETA_History", index=False)
            for ws in writer.book.worksheets:
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for column_cells in ws.columns:
                    max_len = max(len(str(c.value or "")) for c in column_cells)
                    ws.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 35)


    def run():
        if not INPUT_FILE.exists():
            raise FileNotFoundError("shipments.xlsx not found. Run: python create_template.py")
        source = pd.read_excel(INPUT_FILE, sheet_name="Shipments", dtype=str).fillna("")
        required = {"ReferenceNumber"}
        if not required.issubset(source.columns):
            raise ValueError("Shipments sheet must contain a ReferenceNumber column.")

        for col, default in {
            "ReferenceType": "BL", "CarrierName": "", "CarrierCode": "",
            "POL": "", "POD": "", "BaselineETA": "", "Active": "Y"
        }.items():
            if col not in source.columns:
                source[col] = default

        con = db_connect()
        rows = source.to_dict("records")
        active_rows = []
        for raw in rows:
            row = {
                "ReferenceNumber": clean(raw.get("ReferenceNumber")).upper(),
                "ReferenceType": clean(raw.get("ReferenceType")).upper() or "BL",
                "CarrierName": clean(raw.get("CarrierName")),
                "CarrierCode": clean(raw.get("CarrierCode")).upper(),
                "BaselineETA": clean(raw.get("BaselineETA")),
                "POD": clean(raw.get("POD")),
            }
            if row["ReferenceNumber"] and clean(raw.get("Active", "Y")).upper() not in ("N", "NO", "FALSE", "0"):
                active_rows.append(row)

        if not active_rows:
            print("No active shipment references found.")
            return

        print(f"Starting tracking for {len(active_rows)} active shipments...")
        for index, row in enumerate(active_rows, 1):
            ref = row["ReferenceNumber"]
            try:
                data = fetch_tracking(row)
                save_success(con, row, data)
                print(f"[{index}/{len(active_rows)}] OK: {ref} | {data['shipment_status']} | ETA: {data['latest_eta'] or 'not supplied'}")
            except Exception as exc:
                save_error(con, ref, str(exc))
                print(f"[{index}/{len(active_rows)}] ERROR: {ref} | {exc}")
            time.sleep(0.5)  # gentle pacing; provider rate limits still apply

        export_excel(con)
        con.close()
        print(f"Finished. Excel output: {OUTPUT_FILE}")


    if __name__ == "__main__":
        run()
