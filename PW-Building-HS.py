
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"

PORTFOLIO_ID = "2794422276"
TARGET_ACCOUNT_NUMBER = "3020"

CLIENT_ID = os.getenv("PW_CLIENT_ID")
CLIENT_SECRET = os.getenv("PW_CLIENT_SECRET")
SYSTEM_ID = os.getenv("PW_SYSTEM_ID")

if not all([CLIENT_ID, CLIENT_SECRET, SYSTEM_ID]):
    raise RuntimeError(
        "Missing environment variables: "
        "PW_CLIENT_ID, PW_CLIENT_SECRET, or PW_SYSTEM_ID."
    )

HEADERS = {
    "x-propertyware-system-id": SYSTEM_ID,
    "x-propertyware-client-id": CLIENT_ID,
    "x-propertyware-client-secret": CLIENT_SECRET,
    "Accept": "application/json",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# API REQUEST
# ============================================================

def make_api_request(endpoint, params=None):
    url = f"{BASE_URL}{endpoint}"

    response = SESSION.get(
        url,
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


# ============================================================
# RESPONSE HELPERS
# ============================================================

def extract_records(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        possible_keys = (
            "data",
            "items",
            "results",
            "records",
            "glAccounts",
            "accounts",
        )

        for key in possible_keys:
            value = data.get(key)
            if isinstance(value, list):
                return value

        if any(
            key in data
            for key in (
                "accountNumber",
                "account_number",
                "accountName",
            )
        ):
            return [data]

    return []


def get_account_number(account):
    for key in (
        "accountNumber",
        "account_number",
        "accountNo",
        "number",
        "glAccountNumber",
    ):
        if account.get(key) is not None:
            return str(account[key]).strip()

    return ""


def get_account_name(account):
    for key in (
        "accountName",
        "account_name",
        "name",
        "description",
    ):
        if account.get(key) is not None:
            return account[key]

    return ""


# ============================================================
# MAIN TEST
# ============================================================

def main():
    print("=" * 65)
    print("PROPERTYWARE GL ACCOUNT LOOKUP")
    print(f"Portfolio ID: {PORTFOLIO_ID}")
    print(f"Target GL account: {TARGET_ACCOUNT_NUMBER}")
    print("Read-only: no records will be modified")
    print("=" * 65)

    # 1. Verify access to the portfolio.
    portfolio = make_api_request(
        f"/portfolios/{PORTFOLIO_ID}"
    )

    if not isinstance(portfolio, dict):
        print("Unexpected portfolio response:")
        print(json.dumps(portfolio, indent=2))
        return

    print("\nPORTFOLIO DETAILS")
    print(json.dumps({
        "id": portfolio.get("id"),
        "name": portfolio.get("name"),
        "active": portfolio.get("active"),
    }, indent=2))

    # 2. Retrieve the GL account list.
    # This route must be supported by your Propertyware API version.
    gl_data = make_api_request(
        "/accounting/glaccounts",
        params={
            "limit": 100,
            "offset": 0,
        },
    )

    accounts = extract_records(gl_data)

    print(f"\nGL accounts returned: {len(accounts)}")

    # 3. Find account number 3020.
    matches = [
        account
        for account in accounts
        if get_account_number(account) == TARGET_ACCOUNT_NUMBER
    ]

    if matches:
        print("\nMATCHING GL ACCOUNT")
        print(json.dumps(matches, indent=2))

        for account in matches:
            print("\nSUMMARY")
            print(f"Account ID: {account.get('id', 'Not provided')}")
            print(f"Account number: {get_account_number(account)}")
            print(f"Account name: {get_account_name(account)}")

            print(
                "\nNote: the GL account response alone does not prove "
                "that the account is assigned specifically to this "
                "portfolio."
            )
    else:
        print(
            f"\nAccount {TARGET_ACCOUNT_NUMBER} was not found "
            "in the returned records."
        )

        print(
            "The endpoint may use a different route, response format, "
            "or pagination scheme."
        )

        print("\nRESPONSE SAMPLE")
        print(json.dumps(gl_data, indent=2)[:12000])

    print("\nTEST COMPLETE")


if __name__ == "__main__":
    main()
