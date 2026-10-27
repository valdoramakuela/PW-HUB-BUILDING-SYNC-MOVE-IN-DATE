
import os
import time
import json
import requests
from datetime import date, timedelta

# ============================================================
# PROPERTYWARE GENERAL LEDGER — POST DATE DIAGNOSTIC
# Read-only; no HubSpot or Propertyware updates
# ============================================================

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"
PORTFOLIO_ID = "2794422276"
TIMEOUT_SECONDS = 120
PAGE_SIZE = 10

CLIENT_ID = os.getenv("PW_CLIENT_ID")
CLIENT_SECRET = os.getenv("PW_CLIENT_SECRET")
SYSTEM_ID = os.getenv("PW_SYSTEM_ID")

if not all([CLIENT_ID, CLIENT_SECRET, SYSTEM_ID]):
    raise SystemExit(
        "ERROR: Set PW_CLIENT_ID, PW_CLIENT_SECRET, and PW_SYSTEM_ID."
    )

HEADERS = {
    "x-propertyware-system-id": SYSTEM_ID,
    "x-propertyware-client-id": CLIENT_ID,
    "x-propertyware-client-secret": CLIENT_SECRET,
    "Accept": "application/json",
}

# Test one day only, ending on the last known transaction date.
# If your API uses an inclusive end date, this should isolate that day.
POST_DATE = date(2026, 8, 14)

params = {
    "portfolioID": int(PORTFOLIO_ID),
    "postDateStart": POST_DATE.isoformat(),
    "postDateEnd": POST_DATE.isoformat(),
    "limit": PAGE_SIZE,
    "offset": 0,
}

url = f"{BASE_URL}/accounting/generalledger"

print("=" * 70)
print("PROPERTYWARE GENERAL LEDGER — SINGLE POST-DATE TEST")
print("=" * 70)
print("Portfolio ID:", PORTFOLIO_ID)
print("Post date:", POST_DATE.isoformat())
print("Parameters:")
print(json.dumps(params, indent=2))
print("Timeout:", TIMEOUT_SECONDS, "seconds")
print("=" * 70)

started = time.monotonic()

try:
    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=TIMEOUT_SECONDS,
    )

    print("\nHTTP status:", response.status_code)
    print(f"Elapsed: {time.monotonic() - started:.2f} seconds")
    print("Final URL:", response.url)

    try:
        payload = response.json()
    except ValueError:
        print("Non-JSON response:")
        print(response.text[:10000])
        raise SystemExit(1)

    if response.status_code != 200:
        print("\nAPI error:")
        print(json.dumps(payload, indent=2, default=str))
        raise SystemExit(1)

    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = next(
            (
                payload[key]
                for key in ("data", "results", "items", "content", "transactions")
                if isinstance(payload.get(key), list)
            ),
            [],
        )
    else:
        records = []

    print("\nRecords returned:", len(records))

    matching = []
    other_portfolios = []

    for record in records:
        if not isinstance(record, dict):
            continue

        ids = set()
        for key in ("portfolioID", "portfolioId", "portfolio_id"):
            if record.get(key) is not None:
                ids.add(str(record[key]))

        for entry in record.get("glentries", []):
            if isinstance(entry, dict):
                for key in ("portfolioID", "portfolioId", "portfolio_id"):
                    if entry.get(key) is not None:
                        ids.add(str(entry[key]))

        summary = {
            "transactionID": record.get("id"),
            "txDate": record.get("txDate"),
            "lastModifiedDateTime": record.get("lastModifiedDateTime"),
            "transactionType": record.get("transactionType"),
            "portfolioIDs": sorted(ids),
            "glentries": record.get("glentries", []),
        }

        if ids == {PORTFOLIO_ID}:
            matching.append(summary)
        else:
            other_portfolios.append(summary)

    print("\nTarget portfolio records:", len(matching))
    print("Other/mixed/unknown portfolio records:", len(other_portfolios))

    print("\nMATCHING TRANSACTIONS:")
    print(json.dumps(matching, indent=2, default=str)[:20000])

    if other_portfolios:
        print("\nNON-MATCHING RECORDS:")
        print(json.dumps(other_portfolios[:3], indent=2, default=str)[:5000])

    print("\nThis is a date-filter diagnostic, not a balance calculation.")

except requests.exceptions.Timeout:
    print(f"\nTIMEOUT after {time.monotonic() - started:.2f} seconds.")

except requests.exceptions.RequestException as exc:
    print("\nRequest error:", repr(exc))

print("\nDiagnostic finished.")
