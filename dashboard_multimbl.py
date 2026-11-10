
import os
import re
import sqlite3
import math
from datetime import datetime, date
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from dotenv import load_dotenv
from supabase import create_client


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_FILE = BASE_DIR / "eta_tracking.db"

try:
    SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
    SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")
except Exception:
    SUPABASE_URL = ""
    SUPABASE_KEY = ""

SUPABASE_URL = (SUPABASE_URL or os.getenv("SUPABASE_URL", "")).strip()
SUPABASE_KEY = (SUPABASE_KEY or os.getenv("SUPABASE_KEY", "")).strip()

st.set_page_config(
    page_title="Sea Shipment ETA Tracker",
    page_icon="🚢",
    layout="wide"
)

st.title("🚢 Sea Shipment ETA Tracker")
st.caption("Shared shipment monitoring, ETA updates and change history.")

# Refresh dashboard every 10 seconds.
st_autorefresh(
    interval=10 * 1000,
    key="dashboard_auto_refresh"
)


# ============================================================
# GENERAL FUNCTIONS
# ============================================================

def normalize_mbl(value):
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    value = re.sub(r"[\s\-]+", "", str(value).strip().upper())

    if value in {
        "",
        "MBL",
        "BL",
        "BLNUMBER",
        "BILL OF LADING",
        "REFERENCE NUMBER"
    }:
        return ""

    return value


def db_connect():
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    return con


def get_local_data():
    if not DB_FILE.exists():
        return pd.DataFrame(), pd.DataFrame()

    con = db_connect()

    try:
        shipments = pd.read_sql_query(
            "SELECT * FROM current_shipments",
            con
        )

        history = pd.read_sql_query(
            "SELECT * FROM eta_history ORDER BY changed_at DESC",
            con
        )

        return shipments, history

    finally:
        con.close()


def get_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        st.error(
            "Supabase settings are missing. "
            "Check SUPABASE_URL and SUPABASE_KEY "
            "in your .env file or Streamlit secrets."
        )
        st.stop()

    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)

    except Exception as exc:
        st.error(f"Could not connect to Supabase: {exc}")
        st.stop()


def clear_login_state():
    for key in (
        "sb_access_token",
        "sb_refresh_token",
        "sb_user_id",
        "sb_email",
        "sb_role",
        "sb_client"
    ):
        st.session_state.pop(key, None)

# ============================================================
# LOGIN SCREEN
# ============================================================

def login_screen(sb):
    st.subheader("Sign in")

    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        submitted = st.form_submit_button(
            "Login",
            type="primary",
            use_container_width=True
        )

    if submitted:
        if not email.strip() or not password:
            st.warning("Please enter both email and password.")
            return

        try:
            with st.spinner("Verifying login..."):
                result = sb.auth.sign_in_with_password({
                    "email": email.strip(),
                    "password": password
                })

            if not result.session or not result.user:
                st.error("Supabase did not return a valid login session.")
                return

            # Keep this user's client in their Streamlit session.
            st.session_state["sb_client"] = sb
            st.session_state["sb_access_token"] = result.session.access_token
            st.session_state["sb_refresh_token"] = result.session.refresh_token
            st.session_state["sb_user_id"] = result.user.id
            st.session_state["sb_email"] = result.user.email or ""

            st.success("Login successful.")
            st.rerun()

        except Exception as exc:
            st.error(f"Login failed: {exc}")

    st.info(
        "Accounts must be registered in Supabase Authentication "
        "by the administrator."
    )


# ============================================================
# AUTHENTICATED SUPABASE CLIENT
# NO COOKIES
# ============================================================

def authenticated_client():
    sb = get_supabase()

    access = st.session_state.get("sb_access_token")
    refresh = st.session_state.get("sb_refresh_token")

    if not access or not refresh:
        login_screen(sb)
        st.stop()

    try:
        # Restore the session using tokens saved in Streamlit state.
        restored = sb.auth.set_session(access, refresh)

        # Save refreshed tokens if Supabase returned a new session.
        restored_session = getattr(restored, "session", None)

        if restored_session:
            st.session_state["sb_access_token"] = (
                restored_session.access_token
            )

            st.session_state["sb_refresh_token"] = (
                restored_session.refresh_token
            )

        # Verify the current user.
        user_response = sb.auth.get_user()
        user = getattr(user_response, "user", None)

        if not user:
            raise ValueError(
                "Supabase did not return an authenticated user."
            )

        # Keep the latest session tokens.
        try:
            current_session = sb.auth.get_session()

            if current_session:
                st.session_state["sb_access_token"] = (
                    current_session.access_token
                )

                st.session_state["sb_refresh_token"] = (
                    current_session.refresh_token
                )

        except Exception:
            pass

        st.session_state["sb_user_id"] = user.id
        st.session_state["sb_email"] = user.email or ""

        return sb

    except Exception as exc:
        st.error(f"Supabase session validation error: {exc}")

        st.warning(
            "Your login session could not be verified. "
            "You can retry or sign in again."
        )

        col1, col2 = st.columns(2)

        with col1:
            if st.button("Retry session", use_container_width=True):
                st.rerun()

        with col2:
            if st.button("Sign in again", use_container_width=True):
                try:
                    sb.auth.sign_out()
                except Exception:
                    pass

                clear_login_state()
                st.rerun()

        st.stop()


# ============================================================
# USER ROLE
# ============================================================

def get_role(sb, user_id):
    result = (
        sb.table("profiles")
        .select("role")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )

    rows = result.data or []

    if not rows:
        return "user"

    role = str(rows[0].get("role") or "user").strip().lower()

    if role not in ("admin", "user"):
        return "user"

    return role


# ============================================================
# LOAD CLOUD DATA
# ============================================================

def load_cloud_data(sb):
    shipments = (
        sb.table("current_shipments")
        .select("*")
        .order("latest_eta")
        .execute()
        .data or []
    )

    history = (
        sb.table("eta_history")
        .select("*")
        .order("changed_at", desc=True)
        .limit(500)
        .execute()
        .data or []
    )

    return pd.DataFrame(shipments), pd.DataFrame(history)


# ============================================================
# ADD MULTIPLE MBL NUMBERS
# ============================================================

def add_multiple_mbls(sb, mbl_list):
    existing_rows = (
        sb.table("current_shipments")
        .select("reference_number")
        .execute()
        .data or []
    )

    existing = {
        str(row.get("reference_number", "")).strip().upper()
        for row in existing_rows
    }

    added = []
    duplicates = []
    rows = []

    now = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )

    for value in mbl_list:
        mbl = normalize_mbl(value)

        if not mbl:
            continue

        if mbl in existing:
            duplicates.append(mbl)
            continue

        rows.append({
            "reference_number": mbl,
            "reference_type": "MBL",
            "last_checked": now,
            "tracking_result": "PENDING_TRACKING",
            "tracking_error": ""
        })

        existing.add(mbl)
        added.append(mbl)

    if rows:
        sb.table("current_shipments").insert(rows).execute()

    return added, duplicates


# ============================================================
# MANUAL ETA UPDATE
# ============================================================

def save_eta(sb, reference_number, eta_date):
    reference_number = normalize_mbl(reference_number)

    if not reference_number:
        raise ValueError("Please enter an MBL number.")

    new_eta = eta_date.isoformat()

    now = datetime.now().astimezone().isoformat(
        timespec="seconds"
    )

    result = (
        sb.table("current_shipments")
        .select("latest_eta")
        .eq("reference_number", reference_number)
        .limit(1)
        .execute()
    )

    old_eta = ""

    if result.data:
        old_eta = result.data[0].get("latest_eta") or ""

    if old_eta != new_eta:
        sb.table("eta_history").insert({
            "reference_number": reference_number,
            "old_eta": old_eta,
            "new_eta": new_eta,
            "changed_at": now,
            "source": "Manual ETA Update"
        }).execute()

    row = {
        "reference_number": reference_number,
        "reference_type": "MBL",
        "latest_eta": new_eta,
        "eta_is_actual": 0,
        "previous_eta": old_eta if old_eta != new_eta else None,
        "eta_changed_at": now if old_eta != new_eta else None,
        "last_checked": now,
        "tracking_result": "MANUAL_ETA",
        "tracking_error": ""
    }

    sb.table("current_shipments").upsert(
        row,
        on_conflict="reference_number"
    ).execute()


# ============================================================
# ONE-TIME LOCAL DATABASE MIGRATION
# ============================================================

def migrate_local_database(sb):
    local_shipments, local_history = get_local_data()

    if local_shipments.empty and local_history.empty:
        return 0, 0

    def clean_value(value):
        if value is None:
            return None

        if hasattr(value, "item"):
            value = value.item()

        if isinstance(value, float) and not math.isfinite(value):
            return None

        try:
            missing = pd.isna(value)

            if isinstance(missing, bool) and missing:
                return None

        except (TypeError, ValueError):
            pass

        if isinstance(value, (datetime, date)):
            return value.isoformat()

        return value

    shipment_rows = [
        {
            key: clean_value(value)
            for key, value in row.items()
        }
        for row in local_shipments.to_dict("records")
    ]

    history_df = local_history.drop(
        columns=["id"],
        errors="ignore"
    )

    history_rows = [
        {
            key: clean_value(value)
            for key, value in row.items()
        }
        for row in history_df.to_dict("records")
    ]

    if shipment_rows:
        sb.table("current_shipments").upsert(
            shipment_rows,
            on_conflict="reference_number"
        ).execute()

    # WARNING: Running this more than once can duplicate history rows.
    if history_rows:
        sb.table("eta_history").insert(history_rows).execute()

    return len(shipment_rows), len(history_rows)


# ============================================================
# AUTHENTICATION ENTRY POINT
# ============================================================

sb = authenticated_client()

user_id = st.session_state["sb_user_id"]
email = st.session_state["sb_email"]

try:
    role = get_role(sb, user_id)

except Exception as exc:
    st.error(f"Could not read your profile role: {exc}")
    st.stop()

st.session_state["sb_role"] = role


# ============================================================
# SIDEBAR / SIGN OUT
# ============================================================

with st.sidebar:
    st.write(f"**Signed in:** {email}")
    st.write(f"**Role:** {role.upper()}")

    if st.button("Sign out", use_container_width=True):
        try:
            sb.auth.sign_out()
        except Exception:
            pass

        clear_login_state()
        st.rerun()


# ============================================================
# ADMIN TRACKING CONTROL PANEL
# ============================================================

if role == "admin":
    st.divider()
    st.subheader("⚙️ Automatic Tracking Control")

    try:
        worker_rows = (
            sb.table("tracking_worker_status")
            .select("*")
            .eq("id", 1)
            .limit(1)
            .execute()
            .data or []
        )

        settings_rows = (
            sb.table("tracking_settings")
            .select("interval_minutes, enabled")
            .eq("id", 1)
            .limit(1)
            .execute()
            .data or []
        )

        worker_data = worker_rows[0] if worker_rows else {}

        settings_data = (
            settings_rows[0]
            if settings_rows
            else {
                "interval_minutes": 360,
                "enabled": True
            }
        )

        status = worker_data.get("status", "UNKNOWN")
        current_mbl = worker_data.get("current_mbl") or "None"
        heartbeat = worker_data.get("last_heartbeat") or "Not available"

        c1, c2, c3 = st.columns(3)

        c1.metric("Worker Status", status)
        c2.metric("Current MBL", current_mbl)

        c3.metric(
            "Tracking Interval",
            f"{settings_data.get('interval_minutes', 360)} minutes"
        )

        st.caption(f"Last worker heartbeat: {heartbeat}")

        if worker_data.get("message"):
            st.info(worker_data["message"])

        st.markdown("#### Tracking Schedule")

        interval_options = {
            "30 Minutes": 30,
            "6 Hours": 360,
            "12 Hours": 720
        }

        current_interval = settings_data.get(
            "interval_minutes",
            360
        )

        interval_values = list(interval_options.values())

        selected_interval = st.selectbox(
            "Automatic tracking interval",
            list(interval_options.keys()),
            index=(
                interval_values.index(current_interval)
                if current_interval in interval_values
                else 1
            )
        )

        if st.button("💾 Save Tracking Schedule"):
            sb.table("tracking_settings").update({
                "interval_minutes": interval_options[selected_interval],
                "enabled": True,
                "updated_at": datetime.now().astimezone().isoformat()
            }).eq("id", 1).execute()

            st.success("Tracking schedule updated.")
            st.rerun()

        st.markdown("#### Worker Commands")

        col_start, col_stop = st.columns(2)

        with col_start:
            if st.button(
                "▶️ Start Tracking",
                type="primary",
                use_container_width=True
            ):
                pending = (
                    sb.table("tracking_commands")
                    .select("id")
                    .eq("status", "PENDING")
                    .execute()
                    .data or []
                )

                if pending:
                    st.warning("A tracking command is already pending.")

                else:
                    sb.table("tracking_commands").insert({
                        "action": "START",
                        "status": "PENDING",
                        "requested_by": user_id
                    }).execute()

                    st.success("Start command sent to your Windows worker.")
                    st.rerun()

        with col_stop:
            if st.button(
                "⏹️ Stop Tracking",
                use_container_width=True
            ):
                pending = (
                    sb.table("tracking_commands")
                    .select("id")
                    .eq("status", "PENDING")
                    .execute()
                    .data or []
                )

                if pending:
                    st.warning("A tracking command is already pending.")

                else:
                    sb.table("tracking_commands").insert({
                        "action": "STOP",
                        "status": "PENDING",
                        "requested_by": user_id
                    }).execute()

                    st.success(
                        "Stop command sent. It will be processed "
                        "after the current tracking operation."
                    )

                    st.rerun()

        st.caption(
            "The Windows worker must be running to receive commands. "
            "Stop takes effect after the current tracking operation."
        )

    except Exception as exc:
        st.error(f"Unable to load tracking controls: {exc}")


# ============================================================
# ONE-TIME DATABASE MIGRATION
# ============================================================

if role == "admin":
    with st.expander(
        "Admin: one-time local database migration",
        expanded=False
    ):
        st.warning(
            "This imports local SQLite shipment records and ETA history "
            "into Supabase. Run it only once. It does not delete or modify "
            "your local database. Do not run it if you have already migrated."
        )

        confirm = st.checkbox(
            "I have backed up eta_tracking.db and confirm this is the first migration."
        )

        if st.button(
            "Import local records to shared database",
            disabled=not confirm
        ):
            try:
                n_ship, n_hist = migrate_local_database(sb)

                st.success(
                    f"Imported {n_ship} shipment records "
                    f"and {n_hist} history records."
                )

            except Exception as exc:
                st.error(f"Migration failed: {exc}")


# ============================================================
# ADD MULTIPLE MBL NUMBERS
# ============================================================

st.subheader("📥 Add Multiple MBL Numbers")

tab1, tab2 = st.tabs([
    "Paste MBL Numbers",
    "Upload Excel / CSV"
])

with tab1:
    pasted = st.text_area(
        "Paste MBL numbers (one per line)",
        height=150,
        placeholder=(
            "EGLV050650261124\n"
            "MAEU123456789\n"
            "ONEY123456789"
        )
    )

    if st.button(
        "Add Pasted MBLs",
        type="primary",
        use_container_width=True
    ):
        values = [
            normalize_mbl(x)
            for x in re.split(r"[\n,;\t]+", pasted or "")
        ]

        values = [x for x in values if x]

        if not values:
            st.warning("Please enter at least one MBL.")

        else:
            try:
                added, duplicates = add_multiple_mbls(sb, values)

                st.success(f"{len(added)} new MBL(s) added.")

                if duplicates:
                    st.warning(
                        f"{len(duplicates)} duplicate(s) skipped: "
                        f"{', '.join(duplicates)}"
                    )

                st.rerun()

            except Exception as exc:
                st.error(f"Unable to add MBLs: {exc}")


# ============================================================
# UPLOAD EXCEL / CSV
# ============================================================

with tab2:
    uploaded = st.file_uploader(
        "Upload Excel or CSV file",
        type=["xlsx", "xls", "csv"]
    )

    if uploaded:
        try:
            if uploaded.name.lower().endswith(".csv"):
                up_df = pd.read_csv(uploaded, dtype=str)
            else:
                up_df = pd.read_excel(uploaded, dtype=str)

            st.write("File preview:")

            st.dataframe(
                up_df.head(10),
                use_container_width=True
            )

            if len(up_df.columns):
                headers = {
                    str(column).strip().lower().replace("_", " "): column
                    for column in up_df.columns
                }

                preferred = [
                    "mbl",
                    "mbl number",
                    "bl",
                    "bl number",
                    "bill of lading",
                    "reference number"
                ]

                default = next(
                    (
                        headers[h]
                        for h in preferred
                        if h in headers
                    ),
                    up_df.columns[0]
                )

                selected = st.selectbox(
                    "Select the column containing MBL numbers",
                    list(up_df.columns),
                    index=list(up_df.columns).index(default)
                )

                if st.button(
                    "Import MBLs from File",
                    type="primary",
                    use_container_width=True
                ):
                    values = [
                        normalize_mbl(v)
                        for v in up_df[selected].tolist()
                    ]

                    values = [v for v in values if v]

                    added, duplicates = add_multiple_mbls(sb, values)

                    st.success(f"{len(added)} new MBL(s) imported.")

                    if duplicates:
                        st.warning(
                            f"{len(duplicates)} duplicate(s) skipped."
                        )

                    st.rerun()

        except Exception as exc:
            st.error(f"Unable to read file: {exc}")


# ============================================================
# MANUAL ETA UPDATE
# ============================================================

st.divider()
st.subheader("➕ Manual ETA Update")

with st.form("eta_update_form"):
    c1, c2 = st.columns(2)

    with c1:
        ref = st.text_input(
            "MBL Number",
            placeholder="Example: EGLV050650261124"
        )

    with c2:
        eta_date = st.date_input(
            "Latest ETA",
            value=date.today(),
            format="DD-MM-YYYY"
        )

    submitted = st.form_submit_button(
        "💾 Save ETA",
        type="primary",
        use_container_width=True
    )

if submitted:
    try:
        save_eta(sb, ref, eta_date)

        st.success(
            f"ETA saved for {normalize_mbl(ref)} — "
            f"{eta_date.strftime('%d-%b-%Y')}"
        )

        st.rerun()

    except Exception as exc:
        st.error(f"Unable to save ETA: {exc}")


# ============================================================
# LOAD SHIPMENT DATA
# ============================================================

st.divider()

try:
    df, history = load_cloud_data(sb)

except Exception as exc:
    st.error(f"Unable to load shared shipment data: {exc}")
    st.stop()

if df.empty:
    st.info(
        "No shipments added yet. Add MBL numbers above or use the "
        "one-time migration only if it has not already been run."
    )
    st.stop()


# ============================================================
# PREPARE SHIPMENT DATA
# ============================================================

for column in [
    "tracking_error",
    "latest_eta",
    "baseline_eta",
    "eta_is_actual"
]:
    if column not in df.columns:
        df[column] = None

df["latest_eta_dt"] = pd.to_datetime(
    df["latest_eta"],
    errors="coerce"
)

df["baseline_eta_dt"] = pd.to_datetime(
    df["baseline_eta"],
    errors="coerce"
)

df["ETA Alert"] = "ETA NOT AVAILABLE"

has_both = (
    df["latest_eta_dt"].notna()
    & df["baseline_eta_dt"].notna()
)

# Determine delayed / on-time shipments first.
df.loc[
    has_both & (df["latest_eta_dt"] > df["baseline_eta_dt"]),
    "ETA Alert"
] = "DELAYED"

df.loc[
    has_both & (df["latest_eta_dt"] <= df["baseline_eta_dt"]),
    "ETA Alert"
] = "ON TIME"

# An ETA exists but no baseline ETA is available.
has_eta_no_baseline = (
    df["latest_eta_dt"].notna()
    & df["baseline_eta_dt"].isna()
)

df.loc[
    has_eta_no_baseline,
    "ETA Alert"
] = "ETA RECORDED"

# Tracking errors take priority.
df.loc[
    df["tracking_error"].fillna("").astype(str).str.strip() != "",
    "ETA Alert"
] = "TRACKING ERROR"

# Actual arrival takes priority over other alerts.
actual_arrival = (
    pd.to_numeric(
        df["eta_is_actual"],
        errors="coerce"
    ).fillna(0) == 1
)

df.loc[
    actual_arrival,
    "ETA Alert"
] = "ARRIVAL RECORDED"


# ============================================================
# SHIPMENT OVERVIEW
# ============================================================

st.subheader("📊 Shipment Overview")

c1, c2, c3 = st.columns(3)

c1.metric(
    "Total Shipments",
    len(df)
)

c2.metric(
    "Delayed Shipments",
    int((df["ETA Alert"] == "DELAYED").sum())
)

c3.metric(
    "ETAs Recorded",
    int(df["latest_eta"].fillna("").astype(str).ne("").sum())
)


# ============================================================
# SHIPMENT ETA DETAILS
# ============================================================

st.divider()
st.subheader("🚢 Shipment ETA Details")

options = ["All"] + sorted(
    df["ETA Alert"].dropna().unique().tolist()
)

selected_filter = st.selectbox(
    "Filter Shipments",
    options
)

view = (
    df
    if selected_filter == "All"
    else df[df["ETA Alert"] == selected_filter]
)

display_cols = [
    column
    for column in [
        "reference_number",
        "reference_type",
        "carrier",
        "latest_eta",
        "baseline_eta",
        "previous_eta",
        "eta_changed_at",
        "last_checked",
        "tracking_result",
        "tracking_error",
        "ETA Alert"
    ]
    if column in view.columns
]

st.dataframe(
    view[display_cols],
    use_container_width=True,
    hide_index=True
)


# ============================================================
# ETA CHANGE HISTORY
# ============================================================

st.divider()
st.subheader("📅 ETA Change History")

if history.empty:
    st.caption("No ETA changes recorded yet.")

else:
    st.dataframe(
        history,
        use_container_width=True,
        hide_index=True
    )


# ============================================================
# EXPORT DATA
# ============================================================

st.divider()
st.subheader("📥 Export Data")

st.download_button(
    "Download Shipment ETA Report (CSV)",
    data=df.to_csv(index=False).encode("utf-8-sig"),
    file_name="sea_shipment_eta_report.csv",
    mime="text/csv",
    use_container_width=True
)

st.caption(
    "Shipment records and ETA history are stored "
    "in the shared Supabase database."
)
