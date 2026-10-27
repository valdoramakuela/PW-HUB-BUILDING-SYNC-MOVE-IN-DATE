
import os
import json
import requests
from datetime import date
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"

PORTFOLIO_ID = 2794422276
GL_ACCOUNT_ID = 1822294093
GL_ACCOUNT_NUMBER = "3020"

# Current month: October 2026
START_DATE = date(2026, 10, 1)
END_DATE = date(2026, 10, 31)

CLIENT_ID = os.getenv("PW_CLIENT_ID")
CLIENT_SECRET = os.getenv("PW_CLIENT_SECRET")
SYSTEM_ID = os.getenv("PW_SYSTEM_ID")

if not all([CLIENT_ID, CLIENT_SECRET, SYSTEM_ID]):
    raise RuntimeError("Missing Propertyware environment variables.")

HEADERS = {
    "x-propertyware-system-id": SYSTEM_ID,
    "x-propertyware-client-id": CLIENT_ID,
    "x-propertyware-client-secret": CLIENT_SECRET,
    "Accept": "application/json",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def api_get(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"

    response = SESSION.get(
        url,
        params=params,
        timeout=60,
    )

    print(f"\nGET {response.url}")
    print(f"HTTP Status: {response.status_code}")

    if not response.ok:
        print(response.text[:5000])
        return None

    if not response.text.strip():
        return None

    return response.json()


def records_from(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in (
            "data", "items", "results", "records",
            "transactions", "ownerContributions",
        ):
            if isinstance(data.get(key), list):
                return data[key]

    return []


def print_result(title, data):
    print("\n" + "=" * 65)
    print(title)
    print("=" * 65)

    if data is None:
        print("No successful response.")
        return

    print(json.dumps(data, indent=2, default=str)[:25000])


def main():
    print("=" * 65)
    print("PROPERTYWARE MONTHLY PAYMENT / BALANCE CHECK")
    print(f"Portfolio ID: {PORTFOLIO_ID}")
    print(f"GL account: {GL_ACCOUNT_NUMBER} (ID {GL_ACCOUNT_ID})")
    print(f"Date range: {START_DATE} to {END_DATE}")
    print("READ-ONLY")
    print("=" * 65)

    # 1. Portfolio details. portfolioBalance may be null.
    portfolio = api_get(f"/portfolios/{PORTFOLIO_ID}")
    print_result("PORTFOLIO DETAILS / BALANCE FIELD", portfolio)

    # 2. General ledger transactions posted during October.
    # The endpoint requires a date filter.
    ledger = api_get(
        "/accounting/generalledger",
        params={
            "portfolio_id": PORTFOLIO_ID,
            "post_date_start": START_DATE.isoformat(),
            "post_date_end": END_DATE.isoformat(),
            "limit": 100,
            "offset": 0,
        },
    )
    print_result("OCTOBER GENERAL LEDGER TRANSACTIONS", ledger)

    # Show transaction records with account 3020 if the response
    # contains identifiable account fields.
    ledger_records = records_from(ledger)
    matching = []

    for item in ledger_records:
        serialized = json.dumps(item, default=str).lower()

        if (
            GL_ACCOUNT_NUMBER in serialized
            or str(GL_ACCOUNT_ID) in serialized
        ):
            matching.append(item)

    print_result("POSSIBLE OWNER CONTRIBUTION MATCHES", matching)

    # 3. Owner contributions for this portfolio and date range.
    contributions = api_get(
        "/accounting/ownercontributions",
        params={
            "portfolio_id": PORTFOLIO_ID,
            "post_date_start": START_DATE.isoformat(),
            "post_date_end": END_DATE.isoformat(),
            "limit": 100,
            "offset": 0,
        },
    )
    print_result("OCTOBER OWNER CONTRIBUTIONS", contributions)

    # 4. Owner draws are separate from owner contributions.
    draws = api_get(
        "/accounting/ownerdraws",
        params={
            "portfolio_id": PORTFOLIO_ID,
            "post_date_start": START_DATE.isoformat(),
            "post_date_end": END_DATE.isoformat(),
            "limit": 100,
            "offset": 0,
        },
    )
    print_result("OCTOBER OWNER DRAWS", draws)

    print("\nTEST COMPLETE")
    print(
        "Review transaction types and statuses before treating records "
        "as payments. Do not assume a null portfolioBalance is zero."
    )


if __name__ == "__main__":
    main()
