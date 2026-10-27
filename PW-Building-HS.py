
import os
import requests
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"

PORTFOLIO_ID = 2794422276
GL_ACCOUNT_ID = 1822294093
YEAR = 2026

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
    response = SESSION.get(
        f"{BASE_URL}{endpoint}",
        params=params,
        timeout=60,
    )

    print(f"GET {response.url}")
    print(f"HTTP {response.status_code}")

    if not response.ok:
        print(response.text[:1500])
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


def money(value):
    return f"${value:,.2f}"


def main():
    print("=" * 65)
    print("PROPERTYWARE OWNER CONTRIBUTION REPORT")
    print(f"Portfolio: {PORTFOLIO_ID}")
    print(f"GL account: 3020 / ID {GL_ACCOUNT_ID}")
    print(f"Calendar year: {YEAR}")
    print("=" * 65)

    # Portfolio summary
    portfolio = api_get(f"/portfolios/{PORTFOLIO_ID}")

    if isinstance(portfolio, dict):
        print("\nPORTFOLIO SUMMARY")
        print(f"Name: {portfolio.get('name')}")
        print(f"Balance field: {portfolio.get('portfolioBalance')}")

    # Use separate windows no longer than 30 days.
    start = date(YEAR, 1, 1)
    year_end = date(YEAR, 12, 31)
    transactions = {}

    while start <= year_end:
        end = min(start + timedelta(days=29), year_end)

        print(f"\nChecking {start} through {end}")

        data = api_get(
            "/accounting/ownercontributions",
            params={
                "portfolio_id": PORTFOLIO_ID,
                "post_date_start": start.isoformat(),
                "post_date_end": end.isoformat(),
                "limit": 100,
                "offset": 0,
            },
        )

        if data is not None:
            for item in records_from(data):
                if item.get("portfolioID") != PORTFOLIO_ID:
                    continue

                if item.get("glAccountID") != GL_ACCOUNT_ID:
                    continue

                transaction_id = item.get("id")
                if transaction_id is not None:
                    transactions[transaction_id] = item

        start = end + timedelta(days=1)

    # Print only this portfolio's matching transactions.
    print("\n" + "=" * 65)
    print(f"OWNER CONTRIBUTIONS FOR {YEAR}")
    print("=" * 65)

    ordered = sorted(
        transactions.values(),
        key=lambda item: (item.get("date") or "", item.get("id") or 0),
        reverse=True,
    )

    total = 0.0

    if not ordered:
        print("No matching transactions returned.")
    else:
        for item in ordered:
            amount = float(item.get("amount") or 0)
            total += amount

            print(
                f"Date: {item.get('date')} | "
                f"Amount: {money(amount)} | "
                f"Type: {item.get('paymentType')} | "
                f"Reference: {item.get('refNo')} | "
                f"Transaction ID: {item.get('id')}"
            )

        print(f"\nNet amount field total: {money(total)}")

    print("\nCURRENT BALANCE")
    print(f"Portfolio balance returned by API: "
          f"{portfolio.get('portfolioBalance') if isinstance(portfolio, dict) else 'Unavailable'}")
    print(
        "The current balance is not calculated from these transactions; "
        "the API's portfolio response returned null for portfolioBalance."
    )

    print("\nREPORT COMPLETE")


if __name__ == "__main__":
    main()
