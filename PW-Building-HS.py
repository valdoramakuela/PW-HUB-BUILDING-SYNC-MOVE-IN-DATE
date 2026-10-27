
import os
import time
import json
import requests
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# ============================================================
# PROPERTYWARE GENERAL LEDGER — PORTFOLIO RECONCILIATION
# READ-ONLY DIAGNOSTIC
# ============================================================

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"
PORTFOLIO_ID = "2794422276"
TIMEOUT_SECONDS = 120
PAGE_SIZE = 100
MAX_PAGES = 10

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
    "x-propertyware-client-secret": SYSTEM_ID if False else CLIENT_SECRET,
    "Accept": "application/json",
}

# Limit the modification window to 30 days.
end_time = datetime.now(timezone.utc)
start_time = end_time - timedelta(days=30)

DATE_PARAMS = {
    "lastModifiedDateTimeStart": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "lastModifiedDateTimeEnd": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
}

URL = f"{BASE_URL}/accounting/generalledger"


def extract_records(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "content", "transactions"):
            if isinstance(payload.get(key), list):
                return payload[key]
        if "id" in payload and "glentries" in payload:
            return [payload]
    return []


def get_portfolio_ids(record):
    ids = set()

    for key in ("portfolioID", "portfolioId", "portfolio_id"):
        if record.get(key) is not None:
            ids.add(str(record[key]))

    entries = record.get("glentries", [])
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("portfolioID", "portfolioId", "portfolio_id"):
                if entry.get(key) is not None:
                    ids.add(str(entry[key]))

    return ids


all_records = []
seen_ids = set()
account_totals = defaultdict(
    lambda: {"debit": 0.0, "credit": 0.0, "entries": 0}
)
transaction_types = defaultdict(int)
mismatches = []
requests_made = 0
started = time.monotonic()

print("=" * 72)
print("PROPERTYWARE GENERAL LEDGER RECONCILIATION DIAGNOSTIC")
print("=" * 72)
print("Portfolio ID:", PORTFOLIO_ID)
print("Modified from:", DATE_PARAMS["lastModifiedDateTimeStart"])
print("Modified to:  ", DATE_PARAMS["lastModifiedDateTimeEnd"])
print("Page size:", PAGE_SIZE)
print("Maximum pages:", MAX_PAGES)
print("Filter parameter: portfolioID")
print("=" * 72)

for page in range(MAX_PAGES):
    params = {
        **DATE_PARAMS,
        "portfolioID": int(PORTFOLIO_ID),
        "limit": PAGE_SIZE,
        "offset": page * PAGE_SIZE,
    }

    print(f"\nFetching page {page + 1}; offset={params['offset']}...")
    request_started = time.monotonic()

    try:
        response = requests.get(
            URL,
            headers=HEADERS,
            params=params,
            timeout=TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        print("Request failed:", repr(exc))
        break

    requests_made += 1
    print(
        f"HTTP {response.status_code}; "
        f"{time.monotonic() - request_started:.2f}s"
    )

    if response.status_code != 200:
        print("API error:", response.text[:3000])
        break

    try:
        payload = response.json()
    except ValueError:
        print("API returned invalid JSON:", response.text[:3000])
        break

    records = extract_records(payload)

    if not records:
        print("No records in response; pagination finished.")
        break

    new_records = 0

    for record in records:
        if not isinstance(record, dict):
            continue

        record_id = str(record.get("id", ""))

        if record_id and record_id in seen_ids:
            continue
        if record_id:
            seen_ids.add(record_id)

        portfolio_ids = get_portfolio_ids(record)

        # Fail closed: never aggregate a transaction for another portfolio.
        if portfolio_ids != {PORTFOLIO_ID}:
            mismatches.append({
                "transactionID": record.get("id"),
                "portfolioIDs": sorted(portfolio_ids),
            })
            continue

        all_records.append(record)
        new_records += 1
        transaction_types[str(record.get("transactionType", "Unknown"))] += 1

        for entry in record.get("glentries", []):
            if not isinstance(entry, dict):
                continue

            entry_portfolio = entry.get("portfolioID")
            if entry_portfolio is None or str(entry_portfolio) != PORTFOLIO_ID:
                continue

            code = str(entry.get("glaccountCode") or "UNKNOWN")
            gl_id = str(entry.get("glaccountID") or "UNKNOWN")
            key = f"{code} | GL ID {gl_id}"

            try:
                debit = float(entry.get("debit") or 0)
                credit = float(entry.get("credit") or 0)
            except (TypeError, ValueError):
                continue

            account_totals[key]["debit"] += debit
            account_totals[key]["credit"] += credit
            account_totals[key]["entries"] += 1

    print(f"Records in response: {len(records)}; new matching transactions: {new_records}")

    if len(records) < PAGE_SIZE:
        print("Short page received; pagination finished.")
        break

    if new_records == 0:
        print("No new matching transactions; stopping to prevent looping.")
        break

elapsed = time.monotonic() - started

print("\n" + "=" * 72)
print("DIAGNOSTIC RESULTS")
print("=" * 72)
print("HTTP requests:", requests_made)
print("Matching transactions:", len(all_records))
print("Mismatched transactions excluded:", len(mismatches))
print(f"Elapsed time: {elapsed:.2f} seconds")

print("\nTRANSACTION TYPES")
for name, count in sorted(transaction_types.items()):
    print(f"{name}: {count}")

print("\nGL ACCOUNT TOTALS — MODIFIED IN SELECTED WINDOW")
print("-" * 72)
print(f"{'GL ACCOUNT':40} {'DEBITS':>14} {'CREDITS':>14} {'NET (CR-DR)':>14}")

for key, totals in sorted(account_totals.items()):
    net = totals["credit"] - totals["debit"]
    print(
        f"{key[:40]:40} "
        f"{totals['debit']:14.2f} "
        f"{totals['credit']:14.2f} "
        f"{net:14.2f}"
    )

print("\nIMPORTANT LIMITATIONS")
print("- This covers records modified in the selected 30-day window only.")
print("- It is not a complete ledger balance or a verified portfolio balance.")
print("- No data was written to Propertyware or HubSpot.")
print("- Do not treat net credits minus debits as the dashboard balance.")

# Save a local diagnostic report for inspection.
report = {
    "portfolioID": PORTFOLIO_ID,
    "modifiedDateRangeUTC": DATE_PARAMS,
    "requestsMade": requests_made,
    "matchingTransactionCount": len(all_records),
    "mismatchesExcluded": mismatches,
    "transactionTypes": dict(transaction_types),
    "accountTotals": {
        key: value for key, value in sorted(account_totals.items())
    },
    "transactions": all_records,
    "elapsedSeconds": round(elapsed, 2),
}

output_file = "propertyware_portfolio_ledger_diagnostic.json"
with open(output_file, "w", encoding="utf-8") as file:
    json.dump(report, file, indent=2, default=str)

print(f"\nSaved diagnostic report: {output_file}")
print("Diagnostic complete.")
