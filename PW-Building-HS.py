import os
import time
import json
import requests
from datetime import datetime, timezone, timedelta

# ============================================================
# PROPERTYWARE GENERAL LEDGER — PORTFOLIO FILTER DIAGNOSTIC
# ============================================================

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"
PORTFOLIO_ID = "2794422276"
TIMEOUT_SECONDS = 120
RESPONSE_PRINT_LIMIT = 20000

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

# Last seven days, UTC.
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


def extract_records(payload):
    """Extract a list of transaction records from common API response shapes."""
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("data", "results", "items", "content", "transactions"):
            value = payload.get(key)
            if isinstance(value, list):
                return value

        # Some endpoints return a single record.
        if "id" in payload and (
            "glentries" in payload or "transactionType" in payload
        ):
            return [payload]

    return []


def get_record_portfolio_ids(record):
    """Collect portfolio IDs from a transaction and its GL entries."""
    found = set()

    if not isinstance(record, dict):
        return found

    for key in ("portfolioID", "portfolioId", "portfolio_id"):
        value = record.get(key)
        if value is not None:
            found.add(str(value))

    entries = record.get("glentries", [])
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            for key in ("portfolioID", "portfolioId", "portfolio_id"):
                value = entry.get(key)
                if value is not None:
                    found.add(str(value))

    return found


print("=" * 72)
print("PROPERTYWARE GENERAL LEDGER — PORTFOLIO FILTER DIAGNOSTIC")
print("=" * 72)
print(f"Target portfolio: {PORTFOLIO_ID}")
print(f"UTC start:        {params['lastModifiedDateTimeStart']}")
print(f"UTC end:          {params['lastModifiedDateTimeEnd']}")
print(f"Timeout:          {TIMEOUT_SECONDS} seconds")
print(f"Endpoint:         {url}")
print("\nRequest parameters:")
print(json.dumps(params, indent=2))
print("=" * 72)

started = time.monotonic()

try:
    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=TIMEOUT_SECONDS,
    )

    elapsed = time.monotonic() - started

    print(f"\nHTTP status: {response.status_code}")
    print(f"Elapsed:     {elapsed:.2f} seconds")
    print(f"Final URL:   {response.url}")
    print(f"Content-Type: {response.headers.get('Content-Type', 'unknown')}")

    if not response.ok:
        print("\nAPI returned an error response:")
        print(response.text[:RESPONSE_PRINT_LIMIT])
        raise SystemExit(1)

    try:
        payload = response.json()
    except ValueError:
        print("\nERROR: API response was not valid JSON.")
        print(response.text[:RESPONSE_PRINT_LIMIT])
        raise SystemExit(1)

    records = extract_records(payload)

    if not records:
        print("\nNo transaction list was recognized in the response.")
        print("Response structure:")
        print(json.dumps(payload, indent=2, default=str)[:RESPONSE_PRINT_LIMIT])
        raise SystemExit(0)

    target_records = []
    other_records = []
    records_without_portfolio_id = []

    portfolio_counts = {}

    for record in records:
        portfolio_ids = get_record_portfolio_ids(record)

        if not portfolio_ids:
            records_without_portfolio_id.append(record)
        elif PORTFOLIO_ID in portfolio_ids:
            target_records.append(record)
        else:
            other_records.append(record)

        for portfolio_id in portfolio_ids:
            portfolio_counts[portfolio_id] = (
                portfolio_counts.get(portfolio_id, 0) + 1
            )

    print("\nRESULT SUMMARY")
    print("-" * 72)
    print(f"Transactions returned:             {len(records)}")
    print(f"Transactions matching target:      {len(target_records)}")
    print(f"Transactions for other portfolios: {len(other_records)}")
    print(f"Transactions without portfolio ID: {len(records_without_portfolio_id)}")

    print("\nPortfolio IDs found:")
    if portfolio_counts:
        for portfolio_id, count in sorted(portfolio_counts.items()):
            marker = "  <-- TARGET" if portfolio_id == PORTFOLIO_ID else ""
            print(f"  {portfolio_id}: {count} transaction/entry references{marker}")
    else:
        print("  No portfolio IDs found.")

    if other_records:
        print("\nWARNING: The response includes transactions for other portfolios.")
        print("First mismatching transaction examples:")
        for record in other_records[:3]:
            print(
                json.dumps(
                    {
                        "transactionID": record.get("id"),
                        "txDate": record.get("txDate"),
                        "lastModifiedDateTime": record.get("lastModifiedDateTime"),
                        "transactionType": record.get("transactionType"),
                        "portfolioIDs": sorted(get_record_portfolio_ids(record)),
                    },
                    indent=2,
                    default=str,
                )
            )

    if target_records:
        print("\nSample transactions for the requested portfolio:")
        print(
            json.dumps(
                target_records[:3],
                indent=2,
                default=str,
            )[:10000]
        )
    else:
        print(
            "\nNo transactions matching the target portfolio were found "
            "in this response."
        )

    print("\nDIAGNOSTIC CONCLUSION")
    print("-" * 72)

    if other_records and not target_records:
        print(
            "The response contains other portfolio IDs and no target matches. "
            "The portfolio filter is not demonstrably working."
        )
    elif other_records and target_records:
        print(
            "The response contains both target and non-target portfolio IDs. "
            "Do not use this response for a balance calculation without "
            "correctly filtering individual GL entries."
        )
    elif target_records and not other_records:
        print(
            "All identifiable portfolio IDs belong to the target portfolio "
            "in this response. This does not prove the endpoint returns all "
            "transactions needed for a current balance."
        )
    else:
        print(
            "The response does not provide enough portfolio information "
            "to verify the filter."
        )

    print(
        "\nNo balance has been calculated. This script is diagnostic only."
    )

except requests.exceptions.Timeout:
    elapsed = time.monotonic() - started
    print(f"\nTIMEOUT after {elapsed:.2f} seconds.")
    print("The API did not respond within the configured timeout.")

except requests.exceptions.RequestException as exc:
    elapsed = time.monotonic() - started
    print(f"\nREQUEST ERROR after {elapsed:.2f} seconds.")
    print(f"Error type: {type(exc).__name__}")
    print(f"Details: {exc}")

print("\nDiagnostic finished.")
