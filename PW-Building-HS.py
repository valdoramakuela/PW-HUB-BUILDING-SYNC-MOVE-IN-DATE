import os
import json
import requests
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"

CLIENT_ID = os.getenv("PW_CLIENT_ID")
CLIENT_SECRET = os.getenv("PW_CLIENT_SECRET")
SYSTEM_ID = os.getenv("PW_SYSTEM_ID")

PORTFOLIO_ID = 2794422276
TARGET_ACCOUNT_NUMBER = "3020"

if not all([CLIENT_ID, CLIENT_SECRET, SYSTEM_ID]):
    raise ValueError(
        "Missing PW_CLIENT_ID, PW_CLIENT_SECRET, or PW_SYSTEM_ID "
        "in your environment/.env file."
    )

HEADERS = {
    "x-pw-client-id": CLIENT_ID,
    "x-pw-system-id": SYSTEM_ID,
    "Accept": "application/json",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
SESSION.auth = HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET)


def make_api_request(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"

    response = requests.get(
        url,
        headers=HEADERS,
        auth=AUTH,  # Reuse the authentication from your working script
        params=params,
        timeout=60,
    )

    print(f"\nGET {response.url}")
    print(f"HTTP Status: {response.status_code}")

    if not response.ok:
        print("Propertyware error response:")
        print(response.text[:5000])
        response.raise_for_status()

    if not response.text.strip():
        return None

    return response.json()


def extract_records(data):
    """Handle common Propertyware list-response formats."""
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        for key in ("data", "items", "results", "records", "glAccounts"):
            if isinstance(data.get(key), list):
                return data[key]

        # A single account object may be returned directly.
        if any(
            key in data
            for key in ("accountNumber", "account_number", "accountName")
        ):
            return [data]

    return []


def main():
    print("=" * 65)
    print("PROPERTYWARE GL ACCOUNT LOOKUP")
    print(f"Portfolio ID: {PORTFOLIO_ID}")
    print(f"Target GL account number: {TARGET_ACCOUNT_NUMBER}")
    print("Read-only: no records will be modified")
    print("=" * 65)

    # 1. Confirm the portfolio exists.
    portfolio = make_api_request(
        f"/portfolios/{PORTFOLIO_ID}"
    )

    print("\nPORTFOLIO")
    print(json.dumps({
        "id": portfolio.get("id"),
        "name": portfolio.get("name"),
        "active": portfolio.get("active"),
    }, indent=2))

    # 2. Retrieve GL accounts.
    # If your Propertyware API version uses a different route or
    # pagination parameters, adjust this endpoint to match its docs.
    gl_data = make_api_request(
        "/accounting/glaccounts",
        params={
            "limit": 100,
            "offset": 0,
        },
    )

    accounts = extract_records(gl_data)

    print(f"\nGL account records returned: {len(accounts)}")

    # 3. Find account number 3020.
    matches = []

    for account in accounts:
        account_number = account.get(
            "accountNumber",
            account.get("account_number", account.get("number")),
        )

        if str(account_number).strip() == TARGET_ACCOUNT_NUMBER:
            matches.append(account)

    if matches:
        print("\nMATCHING GL ACCOUNT")
        print(json.dumps(matches, indent=2))
    else:
        print(
            "\nNo matching account found in the returned records. "
            "The endpoint may be paginated, use different field names, "
            "or require a different account-number filter."
        )

        print("\nSample response structure:")
        print(json.dumps(gl_data, indent=2)[:12000])

    print("\nTEST COMPLETE")


if __name__ == "__main__":
    main()
