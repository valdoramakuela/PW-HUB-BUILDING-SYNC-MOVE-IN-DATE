import os
import time
import json
import requests
from datetime import datetime, timezone, timedelta

# ============================================================
# PROPERTYWARE GENERAL LEDGER — 120-SECOND TIMEOUT TEST
# ============================================================

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"
PORTFOLIO_ID = "2794422276"
TIMEOUT_SECONDS = 120

CLIENT_ID = os.getenv("PW_CLIENT_ID")
CLIENT_SECRET = os.getenv("PW_CLIENT_SECRET")
SYSTEM_ID = os.getenv("PW_SYSTEM_ID")

if not all([CLIENT_ID, CLIENT_SECRET, SYSTEM_ID]):
    raise SystemExit(
        "ERROR: Missing environment variables. Set "
        "PW_CLIENT_ID, PW_CLIENT_SECRET, and PW_SYSTEM_ID."
    )

HEADERS = {
    "x-propertyware-system-id": SYSTEM_ID,
    "x-propertyware-client-id": CLIENT_ID,
    "x-propertyware-client-secret": CLIENT_SECRET,
    "Accept": "application/json",
}

# Use the same seven-day UTC date-time range for this test.
end_time = datetime.now(timezone.utc)
start_time = end_time - timedelta(days=7)

params = {
    "lastModifiedDateTimeStart": start_time.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    ),
    "lastModifiedDateTimeEnd": end_time.strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    ),
    "portfolio_id": PORTFOLIO_ID,
    "limit": 10,
    "offset": 0,
}

url = f"{BASE_URL}/accounting/generalledger"

print("=" * 70)
print("PROPERTYWARE GENERAL LEDGER DIAGNOSTIC")
print("=" * 70)
print(f"Portfolio ID: {PORTFOLIO_ID}")
print(f"UTC start:    {params['lastModifiedDateTimeStart']}")
print(f"UTC end:      {params['lastModifiedDateTimeEnd']}")
print(f"Timeout:      {TIMEOUT_SECONDS} seconds")
print(f"Endpoint:     {url}")
print("Parameters:")
print(json.dumps(params, indent=2))
print("=" * 70)

started = time.monotonic()

try:
    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=TIMEOUT_SECONDS,
    )

    elapsed = time.monotonic() - started

    print(f"\nHTTP Status: {response.status_code}")
    print(f"Elapsed:     {elapsed:.2f} seconds")
    print(f"Final URL:   {response.url}")
    print(f"Content-Type: {response.headers.get('Content-Type', 'unknown')}")
    print("\nResponse:")
    print("-" * 70)

    try:
        data = response.json()
        print(json.dumps(data, indent=2, default=str)[:20000])
    except ValueError:
        print(response.text[:20000] or "(Empty response body)")

    if response.status_code == 200:
        print("\nRequest completed successfully.")
        print(
            "Note: HTTP 200 does not guarantee that the response contains "
            "the portfolio's current balance."
        )
    else:
        print("\nThe API returned a non-200 status. Review the response above.")

except requests.exceptions.Timeout:
    elapsed = time.monotonic() - started
    print(f"\nTIMEOUT after {elapsed:.2f} seconds.")
    print(
        "The API did not respond within the configured timeout. "
        "This does not establish whether the query is valid."
    )

except requests.exceptions.RequestException as exc:
    elapsed = time.monotonic() - started
    print(f"\nREQUEST ERROR after {elapsed:.2f} seconds.")
    print(f"Error type: {type(exc).__name__}")
    print(f"Details: {exc}")

print("\nDiagnostic finished.")
