
import os
import json
import calendar
import requests
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://api.propertyware.com/pw/api/rest/v1"

CLIENT_ID = os.getenv("PW_CLIENT_ID")
CLIENT_SECRET = os.getenv("PW_CLIENT_SECRET")
SYSTEM_ID = os.getenv("PW_SYSTEM_ID")

PORTFOLIO_ID = 2794422276
OWNER_CONTRIBUTION_GL_ID = 1822294093
OWNER_CONTRIBUTION_GL_NUMBER = "3020"

REPORT_YEAR = 2026
WINDOW_DAYS = 7  # General ledger endpoint may restrict windows to 7 days
PAGE_LIMIT = 100
MAX_PAGES = 100

HEADERS = {
    "x-propertyware-system-id": SYSTEM_ID,
    "x-propertyware-client-id": CLIENT_ID,
    "x-propertyware-client-secret": CLIENT_SECRET,
    "Accept": "application/json",
}

session = requests.Session()
session.headers.update(HEADERS)


# ============================================================
# HELPERS
# ============================================================

def log(message):
    print(message, flush=True)


def money(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def parse_date(value):
    """Convert common Propertyware date formats to date."""
    if not value:
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    text = str(value).strip()

    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(text[:26], fmt).date()
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")
        ).date()
    except ValueError:
        return None


def get_json(path, params=None):
    url = f"{BASE_URL}{path}"

    try:
        response = session.get(
            url,
            params=params,
            timeout=60,
        )
    except requests.RequestException as exc:
        log(f"REQUEST ERROR: {url}: {exc}")
        return None

    log(f"GET {response.url}")
    log(f"HTTP {response.status_code}")

    if not response.ok:
        log(f"ERROR RESPONSE: {response.text[:2000]}")
        return None

    try:
        return response.json()
    except ValueError:
        log("ERROR: Response was not valid JSON.")
        log(response.text[:1000])
        return None


def extract_records(payload):
    """Support common API response shapes."""
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in ("data", "results", "items", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    # Some endpoints return one record directly.
    if any(key in payload for key in ("portfolioID", "portfolioId")):
        return [payload]

    return []


def get_field(record, *names):
    """Case-insensitive field lookup."""
    if not isinstance(record, dict):
        return None

    normalized = {
        str(key).replace("_", "").lower(): value
        for key, value in record.items()
    }

    for name in names:
        key = name.replace("_", "").lower()
        if key in normalized:
            return normalized[key]

    return None


def paginate_endpoint(path, base_params):
    """
    Retrieve all pages. Endpoint-specific filtering is still verified
    locally because Propertyware may return records outside the filters.
    """
    all_records = []

    for page in range(MAX_PAGES):
        params = dict(base_params)
        params["limit"] = PAGE_LIMIT
        params["offset"] = page * PAGE_LIMIT

        payload = get_json(path, params)

        if payload is None:
            break

        records = extract_records(payload)
        if not records:
            break

        all_records.extend(records)

        if len(records) < PAGE_LIMIT:
            break

    return all_records


def date_windows(start_date, end_date, window_days=WINDOW_DAYS):
    """Create inclusive date windows, each no longer than window_days."""
    current = start_date

    while current <= end_date:
        window_end = min(
            current + timedelta(days=window_days - 1),
            end_date,
        )

        yield current, window_end
        current = window_end + timedelta(days=1)


# ============================================================
# PORTFOLIO DETAILS
# ============================================================

def get_portfolio():
    log("\n" + "=" * 70)
    log("PORTFOLIO DETAILS")
    log("=" * 70)

    payload = get_json(f"/portfolios/{PORTFOLIO_ID}")

    if not isinstance(payload, dict):
        log("Could not retrieve portfolio details.")
        return None

    log(json.dumps(payload, indent=2, default=str))

    log(f"\nPortfolio ID: {PORTFOLIO_ID}")
    log(f"Portfolio name: {payload.get('name')}")
    log(f"API portfolio balance: {payload.get('portfolioBalance')}")

    if payload.get("portfolioBalance") is None:
        log(
            "NOTE: Propertyware returned a null portfolioBalance. "
            "This script will not treat it as zero."
        )

    return payload


# ============================================================
# GENERAL LEDGER
# ============================================================

def get_general_ledger(start_date, end_date):
    """
    Query the general ledger using short date windows.
    The API requires a date filter and may restrict date ranges.
    """
    log("\n" + "=" * 70)
    log(f"GENERAL LEDGER: {start_date} through {end_date}")
    log("=" * 70)

    results = []

    for window_start, window_end in date_windows(
        start_date, end_date
    ):
        log(
            f"\nWindow: {window_start} through {window_end}"
        )

        params = {
            "portfolio_id": PORTFOLIO_ID,
            "post_date_start": window_start.isoformat(),
            "post_date_end": window_end.isoformat(),
        }

        records = paginate_endpoint(
            "/accounting/generalledger",
            params,
        )

        log(f"Records returned in window: {len(records)}")

        for record in records:
            portfolio_value = get_field(
                record, "portfolioID", "portfolioId"
            )
            transaction_date = parse_date(
                get_field(
                    record,
                    "postDate",
                    "date",
                    "transactionDate",
                )
            )

            # Verify the endpoint filters ourselves.
            if str(portfolio_value) != str(PORTFOLIO_ID):
                continue

            if transaction_date is None:
                continue

            if not (window_start <= transaction_date <= window_end):
                continue

            results.append(record)

    return results


# ============================================================
# OWNER CONTRIBUTIONS
# ============================================================

def get_owner_contributions(start_date, end_date):
    log("\n" + "=" * 70)
    log(f"OWNER CONTRIBUTIONS: {start_date} through {end_date}")
    log("=" * 70)

    matching = []
    seen_ids = set()

    for window_start, window_end in date_windows(
        start_date, end_date
    ):
        log(
            f"\nWindow: {window_start} through {window_end}"
        )

        params = {
            "portfolio_id": PORTFOLIO_ID,
            "post_date_start": window_start.isoformat(),
            "post_date_end": window_end.isoformat(),
            "orderby": "postdate",
        }

        records = paginate_endpoint(
            "/accounting/ownercontributions",
            params,
        )

        log(f"Raw records returned: {len(records)}")

        # Diagnostic: inspect the first records returned by the API.
        for sample in records[:3]:
            log(
                "API sample: "
                + json.dumps(sample, default=str)[:1000]
            )

        for record in records:
            portfolio_value = get_field(
                record, "portfolioID", "portfolioId"
            )

            transaction_date = parse_date(
                get_field(
                    record,
                    "postDate",
                    "date",
                    "transactionDate",
                )
            )

            # Ignore records without a matching portfolio ID.
            if str(portfolio_value) != str(PORTFOLIO_ID):
                continue

            # Ignore records outside the requested dates.
            if transaction_date is None:
                continue

            if not (window_start <= transaction_date <= window_end):
                continue

            # Check the GL account using the top-level field and
            # transaction lines when available.
            gl_account_value = get_field(
                record,
                "glAccountID",
                "glAccountId",
            )

            lines = get_field(record, "lines")
            line_matches = []

            if isinstance(lines, list):
                for line in lines:
                    line_gl = get_field(
                        line,
                        "glAccountID",
                        "glAccountId",
                        "accountID",
                        "accountId",
                    )

                    if str(line_gl) == str(OWNER_CONTRIBUTION_GL_ID):
                        line_matches.append(line)

            account_matches = (
                str(gl_account_value)
                == str(OWNER_CONTRIBUTION_GL_ID)
                or bool(line_matches)
            )

            if not account_matches:
                continue

            transaction_id = get_field(record, "id")

            # Avoid duplicate records if pagination/windows overlap.
            dedupe_key = (
                str(transaction_id)
                if transaction_id is not None
                else json.dumps(record, sort_keys=True, default=str)
            )

            if dedupe_key in seen_ids:
                continue

            seen_ids.add(dedupe_key)
            matching.append(record)

    return matching


# ============================================================
# REPORTING
# ============================================================

def print_contributions(records):
    log("\n" + "=" * 70)
    log("MATCHING OWNER CONTRIBUTIONS")
    log("=" * 70)

    if not records:
        log("No matching transactions returned.")
        log(
            "Check the diagnostic samples above. If they contain "
            "other portfolios or dates, the endpoint may be ignoring "
            "filters or using different field names."
        )
        return Decimal("0")

    total = Decimal("0")

    for record in sorted(
        records,
        key=lambda item: str(
            get_field(item, "postDate", "date")
        ),
    ):
        amount = money(get_field(record, "amount"))
        total += amount

        log(
            f"Date: {get_field(record, 'postDate', 'date')} | "
            f"ID: {get_field(record, 'id')} | "
            f"Portfolio: {get_field(record, 'portfolioID', 'portfolioId')} | "
            f"GL: {get_field(record, 'glAccountID', 'glAccountId')} | "
            f"Ref: {get_field(record, 'refNo', 'referenceNumber')} | "
            f"Amount: ${amount:,.2f}"
        )

    log("-" * 70)
    log(f"Total matching contributions: ${total:,.2f}")
    log(
        "IMPORTANT: This is a contribution total, not the current "
        "portfolio balance."
    )

    return total


def main():
    if not all((CLIENT_ID, CLIENT_SECRET, SYSTEM_ID)):
        raise RuntimeError(
            "Missing required environment variables. Set "
            "PW_CLIENT_ID, PW_CLIENT_SECRET, and PW_SYSTEM_ID."
        )

    start_date = date(REPORT_YEAR, 1, 1)
    end_date = date(REPORT_YEAR, 12, 31)

    log("=" * 70)
    log("PROPERTYWARE PORTFOLIO ACCOUNTING DIAGNOSTIC")
    log(f"Portfolio ID: {PORTFOLIO_ID}")
    log(f"Owner Contribution GL ID: {OWNER_CONTRIBUTION_GL_ID}")
    log(f"Report year: {REPORT_YEAR}")
    log("=" * 70)

    portfolio = get_portfolio()

    contributions = get_owner_contributions(
        start_date,
        end_date,
    )

    contribution_total = print_contributions(contributions)

    log("\n" + "=" * 70)
    log("SUMMARY")
    log("=" * 70)

    log(f"Portfolio ID: {PORTFOLIO_ID}")
    log(f"Owner Contribution total for {REPORT_YEAR}: "
        f"${contribution_total:,.2f}")

    if portfolio:
        balance = portfolio.get("portfolioBalance")
        if balance is None:
            log("Current portfolio balance: NOT PROVIDED BY THIS API RESPONSE")
        else:
            log(f"Current portfolio balance: ${money(balance):,.2f}")

    log(
        "\nThe Propertyware dashboard balance must be obtained from "
        "an endpoint or report that exposes the actual portfolio ledger "
        "balance. Do not assume owner contributions equal that balance."
    )


if __name__ == "__main__":
    main()
