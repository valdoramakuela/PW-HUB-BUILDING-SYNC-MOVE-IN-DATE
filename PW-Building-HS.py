
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
    "lastModifiedDateStart": "2026-10-26",
    "lastModifiedDateEnd": "2026-10-27",
    "post_date_start": "2026-10-26",
    "post_date_end": "2026-10-26",
    "limit": 10,
    "offset": 0,
}


url = f"{BASE_URL}/accounting/generalledger"

try:
    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=30,
    )

    print("HTTP:", response.status_code)
    print("URL:", response.url)
    print("Content-Type:", response.headers.get("Content-Type"))
    print("RAW RESPONSE:")
    print(response.text[:5000])

    try:
        data = response.json()
        print("\nPARSED RESPONSE:")
        print(json.dumps(data, indent=2, default=str))
    except ValueError:
        print("\nResponse is not JSON; raw response shown above.")

except requests.RequestException as exc:
    print("Request failed:", str(exc))
