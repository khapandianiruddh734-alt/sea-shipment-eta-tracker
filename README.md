# Sea Shipment ETA Automation (Excel + Python)

## What this starter project does
- Reads multiple ocean shipment references from `shipments.xlsx`.
- Calls Tracking One's documented ocean tracking API using a bearer API key.
- Supports BL, container and booking references where the provider supports them.
- Saves current data and ETA changes in SQLite.
- Exports `shipment_tracking_output.xlsx`.
- Displays dashboard indicators for delayed vs baseline ETA, arrival recorded and tracking errors.

**Important:** Track-Trace is a tracking website, not the source of a documented public API in this project. This starter uses Tracking One as an API provider. Confirm Evergreen/other carrier coverage, pricing, credits, and terms with the provider before loading 100–500 live shipments. Do not assume every carrier or HBL is supported.

## 1. Install
1. Install Python 3.11 or newer from https://www.python.org/downloads/
2. During installation, tick **Add Python to PATH**.
3. Extract this ZIP into a folder, e.g. `C:\\SeaETA`.
4. Open that folder in VS Code.
5. Open Terminal > New Terminal.

Run:

```powershell
py -m venv .venv
.venv\\Scripts\\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation, use Command Prompt and run:
```bat
.venv\\Scripts\\activate.bat
```

## 2. Create Excel input
Run:
```powershell
python create_template.py
```
Open `shipments.xlsx`. One shipment per row in the `Shipments` sheet.

| Column | Meaning |
|---|---|
| ReferenceNumber | Full BL / container / booking number |
| ReferenceType | `BL`, `CONTAINER`, or `BOOKING` |
| CarrierName | For your own reference, e.g. EVERGREEN |
| CarrierCode | Optional SCAC/provider carrier code; leave blank if unsure |
| POL | Optional origin port |
| POD | Destination port, e.g. Mundra |
| BaselineETA | Original ETA in `YYYY-MM-DD` format, for delay comparison |
| Active | `Y` to track; `N` to skip |

For your sample, use:
`EGLV050650261124 | BL | EVERGREEN | EGLV | | Mundra | | Y`

The carrier code is optional. If the provider does not recognise `EGLV`, remove it and retry without the code. The provider may require an associated container number for some BLs.

## 3. Obtain API access
1. Read the provider's documentation: https://developer.tracking.one/getting-started/
2. Check supported ocean carriers: https://api.tracking.one/v1/carriers/ocean
3. Create an account/API key from the provider dashboard.
4. Review current pricing, monthly shipment limits, supported carrier lookup types and data terms before use.
5. Copy `.env.example` to `.env` and add your key:
```env
TRACKING_ONE_API_KEY=YOUR_REAL_KEY
```
Never paste your API key into chat, Excel, GitHub or source code.

## 4. Run a small test first
Start with one or two references:
```powershell
python tracker.py
```
Expected outputs:
- `eta_tracking.db` — internal database and ETA history
- `shipment_tracking_output.xlsx` — current tracking and ETA history

If you get HTTP 401, check the key. HTTP 402 means usage/credits. HTTP 429 means rate limit. "Shipment not found" or unsupported type means the provider/carrier could not resolve that reference; verify the BL and try the carrier's own portal. A failed lookup does not erase the previous successful ETA.

## 5. Open dashboard
In another VS Code terminal:
```powershell
.venv\\Scripts\\Activate.ps1
streamlit run dashboard.py
```
The dashboard opens in your browser. Keep the database and scripts in the same folder.

The dashboard's DELAYED indicator compares `latest_eta` with `BaselineETA`. If you do not enter a baseline ETA, it shows "No baseline" rather than guessing whether the shipment is late.

## 6. Schedule every 6 hours (Windows Task Scheduler)
1. Find the full path of `python.exe` inside `.venv\\Scripts\\python.exe`.
2. Open **Task Scheduler** > **Create Task**.
3. Name: `Sea Shipment ETA Refresh`.
4. Trigger: Daily; Advanced settings: Repeat task every **6 hours** for **Indefinitely**.
5. Action: Start a program.
   - Program: full path to `.venv\\Scripts\\python.exe`
   - Arguments: full path to `tracker.py`
   - Start in: project folder path, e.g. `C:\\SeaETA`
6. In Settings, enable "Run task as soon as possible after a scheduled start is missed".
7. Save and run the task manually once to test.

The PC must be powered on, connected to the internet, and allowed to run scheduled tasks. For always-on monitoring, use an always-on Windows machine or a managed server. Do not promise 24/7 operation from a sleeping laptop.

## 7. Operational safeguards
- Begin with 2–5 test shipments; verify against Evergreen's official ShipmentLink and other carrier portals.
- Compare provider ETA, vessel, POL/POD and event times against carrier records.
- Increase to 100–500 only after confirming carrier coverage and pricing.
- Keep a copy of the input workbook and database backups.
- Treat carrier ETA as an estimate, not customs clearance or IGM confirmation.
- For production use, add access controls and backups if multiple people use the dashboard.
