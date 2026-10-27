
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"
PORTFOLIO_ID = 2794422276

headers = {
    "x-propertyware-system-id": os.getenv("PW_SYSTEM_ID"),
    "x-propertyware-client-id": os.getenv("PW_CLIENT_ID"),
    "x-propertyware-client-secret": os.getenv("PW_CLIENT_SECRET"),
    "Accept": "application/json",
}

url = f"{BASE_URL}/portfolios/{PORTFOLIO_ID}"

response = requests.get(url, headers=headers, timeout=30)

print("HTTP:", response.status_code)
response.raise_for_status()

data = response.json()

print("\nFULL PORTFOLIO RESPONSE:")
print(json.dumps(data, indent=2, default=str))

print("\nBALANCE-RELATED FIELDS:")
def find_balance_fields(obj, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key

            if any(word in key.lower() for word in (
                "balance", "ledger", "escrow", "reserve", "equity"
            )):
                print(f"{current_path} = {value!r}")

            find_balance_fields(value, current_path)

    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            find_balance_fields(value, f"{path}[{index}]")

find_balance_fields(data)
