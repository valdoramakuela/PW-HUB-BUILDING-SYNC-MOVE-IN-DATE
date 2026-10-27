import os
import time
import json
import requests
from datetime import datetime, timezone, timedelta

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"
PORTFOLIO_ID = "2794422276"
TIMEOUT_SECONDS = 120

CLIENT_ID = os.getenv("PW_CLIENT_ID")
CLIENT_SECRET = os.getenv("PW_CLIENT_SECRET")
SYSTEM_ID = os.getenv("PW_SYSTEM_ID")

if not all([CLIENT_ID, CLIENT_SECRET, SYSTEM_ID]):
    raise SystemExit(
        "Missing environment variables: PW_CLIENT_ID, "
        "PW_CLIENT_SECRET, PW_SYSTEM_ID"
    )

HEADERS = {
    "x-propertyware-system-id": SYSTEM_ID,
    "x-propertyware-client-id": CLIENT_ID,
    "x-propertyware-client-secret": CLIENT_SECRET,
    "Accept": "application/json",
}

end_time = datetime.now(timezone.utc)
start_time = end_time - timedelta(days=7)

# Controlled test: use portfolioID instead of portfolio_id.
params = {
    "lastModifiedDateTimeStart": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "lastModifiedDateTimeEnd": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "portfolioID": int(PORTFOLIO_ID),
    "limit": 3,
    "offset": 0,
}

url = f"{BASE_URL}/accounting/generalledger"

print("=" * 70)
print("PROPERTYWARE GENERAL LEDGER — ALTERNATE FILTER TEST")
print("=" * 70)
print("Target portfolio:", PORTFOLIO_ID)
print("Parameters:", json.dumps(params, indent=2))
print("Timeout:", TIMEOUT_SECONDS, "seconds")

started = time.monotonic()

try:
    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=TIMEOUT_SECONDS,
    )

    elapsed = time.monotonic() - started

    print("\nHTTP status:", response.status_code)
    print(f"Elapsed: {elapsed:.2f} seconds")
    print("Final URL:", response.url)

    try:
        payload = response.json()
    except ValueError:
        print("Response was not JSON:")
        print(response.text[:10000])
        raise SystemExit(1)

    if response.status_code != 200:
        print(json.dumps(payload, indent=2, default=str)[:10000])
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

    target_count = 0
    other_count = 0
    unknown_count = 0

    for record in records:
        ids = set()

        if isinstance(record, dict):
            for key in ("portfolioID", "portfolioId", "portfolio_id"):
                if record.get(key) is not None:
                    ids.add(str(record[key]))

            entries = record.get("glentries", [])
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict):
                        for key in ("portfolioID", "portfolioId", "portfolio_id"):
                            if entry.get(key) is not None:
                                ids.add(str(entry[key]))

        if not ids:
            unknown_count += 1
        elif PORTFOLIO_ID in ids:
            target_count += 1
        else:
            other_count += 1

        print(
            json.dumps(
                {
                    "transactionID": record.get("id") if isinstance(record, dict) else None,
                    "txDate": record.get("txDate") if isinstance(record, dict) else None,
                    "lastModifiedDateTime": (
                        record.get("lastModifiedDateTime")
                        if isinstance(record, dict) else None
                    ),
                    "portfolioIDs": sorted(ids),
                },
                default=str,
            )
        )

    print("\nSUMMARY")
    print("Target portfolio records:", target_count)
    print("Other portfolio records:", other_count)
    print("Records without portfolio ID:", unknown_count)

    if target_count and not other_count:
        print("RESULT: This parameter spelling returned only target portfolio records.")
    elif other_count:
        print("RESULT: Other portfolios are still present; the filter remains unverified.")
    else:
        print("RESULT: No target records found; this test is inconclusive.")

except requests.exceptions.Timeout:
    print(f"\nRequest timed out after {time.monotonic() - started:.2f} seconds.")

except requests.exceptions.RequestException as exc:
    print("\nRequest error:", repr(exc))

print("\nDiagnostic finished.")
