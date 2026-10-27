
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"
PORTFOLIO_ID = "2794422276"
ENDPOINT = f"{BASE_URL}/portfolios/{PORTFOLIO_ID}"

client_id = os.getenv("PW_CLIENT_ID")
client_secret = os.getenv("PW_CLIENT_SECRET")
system_id = os.getenv("PW_SYSTEM_ID")

if not all([client_id, client_secret, system_id]):
    raise SystemExit(
        "Missing PW_CLIENT_ID, PW_CLIENT_SECRET, or PW_SYSTEM_ID."
    )

headers = {
    "x-propertyware-client-id": client_id,
    "x-propertyware-client-secret": client_secret,
    "x-propertyware-system-id": system_id,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

print("=" * 65)
print("PROPERTYWARE SINGLE PORTFOLIO TEST")
print("Method: GET")
print(f"Portfolio ID: {PORTFOLIO_ID}")
print(f"Endpoint: {ENDPOINT}")
print("Read-only: no records will be modified")
print("=" * 65)

try:
    response = requests.get(
        ENDPOINT,
        headers=headers,
        timeout=(30, 60),
    )

    print(f"\nHTTP Status: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type', 'unknown')}")

    try:
        data = response.json()
        print("\nResponse:")
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except ValueError:
        print("\nNon-JSON response:")
        print(response.text[:8000])

except requests.RequestException as exc:
    print(f"\nRequest failed: {exc}")

print("\nTEST COMPLETE")
