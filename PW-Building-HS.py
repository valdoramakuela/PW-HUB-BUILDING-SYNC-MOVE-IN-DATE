
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"

headers = {
    "x-propertyware-system-id": os.getenv("PW_SYSTEM_ID"),
    "x-propertyware-client-id": os.getenv("PW_CLIENT_ID"),
    "x-propertyware-client-secret": os.getenv("PW_CLIENT_SECRET"),
    "Accept": "application/json",
}

params = {
    "portfolio_id": 2794422276,
    "post_date_start": "2026-10-20",
    "post_date_end": "2026-10-27",
    "limit": 10,
    "offset": 0,
}

url = f"{BASE_URL}/accounting/generalledger"

response = requests.get(
    url,
    headers=headers,
    params=params,
    timeout=30,
)

print("HTTP:", response.status_code)
print("URL:", response.url)
print(json.dumps(response.json(), indent=2, default=str))
