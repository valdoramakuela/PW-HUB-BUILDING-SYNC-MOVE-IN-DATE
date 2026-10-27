
import os
import json
import requests
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"

PORTFOLIO_ID = 2794422276
OWNER_STATEMENT_REPORT_ID = 3227549696
OWNER_CONTRIBUTION_GL_ID = 1822294093

# Keep this under 30 days.
END_DATE = date.today()
START_DATE = END_DATE - timedelta(days=6)

HEADERS = {
    "x-propertyware-system-id": os.getenv("PW_SYSTEM_ID"),
    "x-propertyware-client-id": os.getenv("PW_CLIENT_ID"),
    "x-propertyware-client-secret": os.getenv("PW_CLIENT_SECRET"),
    "Accept": "application/json",
}

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# HTTP HELPERS
# ============================================================

def request_json(path, params=None):
    url = f"{BASE_URL}{path}"

    try:
        response = session.get(
            url,
            params=params,
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"\nREQUEST FAILED: {exc}")
        return None

    print(f"\nGET {response.url}")
    print(f"HTTP: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type')}")

    try:
        payload = response.json()
    except ValueError:
        print("Response was not JSON:")
        print(response.text[:3000])
        return None

    if not response.ok:
        print("API ERROR:")
        print(json.dumps(payload, indent=2, default=str))
        return None

    return payload


def find_balance_fields(obj, path=""):
    """Print fields whose names may relate to balances."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key

            if any(term in key.lower() for term in (
                "balance",
                "ledger",
                "escrow",
                "reserve",
                "equity",
                "statement",
            )):
                print(f"{current_path}: {value!r}")

            find_balance_fields(value, current_path)

    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            find_balance_fields(value, f"{path}[{index}]")


def print_payload_summary(payload):
    if isinstance(payload, list):
        print(f"Records returned: {len(payload)}")
        if payload:
            print("First record:")
            print(json.dumps(payload[0], indent=2, default=str))

    elif isinstance(payload, dict):
        print("Response keys:", list(payload.keys()))
        print(json.dumps(payload, indent=2, default=str)[:10000])

    else:
        print(repr(payload))


# ============================================================
# 1. GET PORTFOLIO DETAILS
# ============================================================

def get_portfolio():
    print("\n" + "=" * 70)
    print("1. PORTFOLIO DETAILS")
    print("=" * 70)

    payload = request_json(f"/portfolios/{PORTFOLIO_ID}")

    if not isinstance(payload, dict):
        print("Could not retrieve portfolio.")
        return None

    print("\nPortfolio name:", payload.get("name"))
    print("Portfolio ID:", payload.get("id"))
    print("portfolioBalance:", payload.get("portfolioBalance"))
    print(
        "Owner statement report ID:",
        payload.get("ownerStatementReportID"),
    )

    print("\nBalance-related fields:")
    find_balance_fields(payload)

    if payload.get("portfolioBalance") is None:
        print(
            "\nThe API returned portfolioBalance=null. "
            "This is not the same as a zero balance."
        )

    return payload


# ============================================================
# 2. TEST GENERAL LEDGER
# ============================================================

def test_general_ledger():
    print("\n" + "=" * 70)
    print("GENERAL LEDGER — UTC DATE-TIME TEST")
    print("=" * 70)

    from datetime import datetime, timezone, timedelta

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    params = {
        "lastModifiedDateTimeStart": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lastModifiedDateTimeEnd": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "portfolio_id": PORTFOLIO_ID,
        "limit": 10,
        "offset": 0,
    }

    try:
        response = session.get(
            f"{BASE_URL}/accounting/generalledger",
            params=params,
            timeout=30,
        )

        print("HTTP:", response.status_code)
        print("URL:", response.url)
        print("Response:", response.text[:5000])

        if response.ok:
            try:
                payload = response.json()
            except ValueError:
                print("Response was not JSON.")
                return

            print("\nReturned ledger data:")
            print(json.dumps(payload, indent=2, default=str)[:12000])
            print("\nBalance-related fields:")
            find_balance_fields(payload)

    except requests.RequestException as exc:
        print("Request failed:", exc)


# ============================================================
# 3. TEST OWNER CONTRIBUTIONS ONCE
# ============================================================

def test_owner_contributions():
    print("\n" + "=" * 70)
    print("3. OWNER CONTRIBUTIONS DIAGNOSTIC")
    print("=" * 70)

    params = {
        "portfolio_id": PORTFOLIO_ID,
        "post_date_start": START_DATE.isoformat(),
        "post_date_end": END_DATE.isoformat(),
        "limit": 10,
        "offset": 0,
    }

    payload = request_json(
        "/accounting/ownercontributions",
        params=params,
    )

    if payload is None:
        print("Owner-contributions test failed.")
        return

    print("\nOwner-contributions response:")
    print_payload_summary(payload)

    print(
        "\nNote: this endpoint has previously returned records "
        "for unrelated portfolios. Verify each record's portfolio "
        "ID and transaction date before using it."
    )


# ============================================================
# MAIN
# ============================================================

def main():
    missing = [
        name for name in (
            "PW_SYSTEM_ID",
            "PW_CLIENT_ID",
            "PW_CLIENT_SECRET",
        )
        if not os.getenv(name)
    ]

    if missing:
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing)
        )

    print("=" * 70)
    print("PROPERTYWARE PORTFOLIO BALANCE DIAGNOSTIC")
    print("=" * 70)
    print("Portfolio ID:", PORTFOLIO_ID)
    print("Owner statement report ID:", OWNER_STATEMENT_REPORT_ID)
    print("Owner Contribution GL ID:", OWNER_CONTRIBUTION_GL_ID)

    portfolio = get_portfolio()

    test_general_ledger()

    # One small request only. No loops or large pagination scans.
    test_owner_contributions()

    print("\n" + "=" * 70)
    print("RESULT")
    print("=" * 70)

    if portfolio:
        balance = portfolio.get("portfolioBalance")
        if balance is None:
            print("Current balance: not provided by portfolio endpoint.")
        else:
            print("Portfolio balance:", balance)

    print(
        "\nThis diagnostic does not calculate a balance from "
        "contributions. A verified ledger/report source is needed "
        "to establish the current balance accurately."
    )


if __name__ == "__main__":
    main()
