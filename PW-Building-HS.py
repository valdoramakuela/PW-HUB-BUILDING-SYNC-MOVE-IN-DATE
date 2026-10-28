import json
import logging
import os
import time
from collections import defaultdict
from datetime import date, datetime

import requests
from dotenv import load_dotenv
from hubspot import HubSpot
from hubspot.crm.objects import ApiException
from hubspot.crm.objects.models import (
    Filter,
    FilterGroup,
    PublicObjectSearchRequest,
    SimplePublicObjectInput,
)


# ============================================================
# PROPERTYWARE -> HUBSPOT UNIT UPCOMING MOVE-IN SYNC
# ============================================================
#
# SAFETY RULE:
#
# This script is UPDATE-ONLY in HubSpot.
#
# - It NEVER creates HubSpot Units.
# - It matches the Propertyware unit idNumber against:
#
#       HubSpot geekly_unit_id
#
# - It updates:
#
#       upcoming_move_in_date
#
# - Draft Propertyware leases are used only as source data.
# - Draft leases are NOT created in HubSpot.
#
# ------------------------------------------------------------
# ID NOTE:
#
# Propertyware exposes two different identifiers for units
# (same pattern as leases and buildings):
#
#     lease["unitID"]
#         Propertyware's INTERNAL unit object ID.
#
#     unit["idNumber"]
#         The unit number shown in the Propertyware UI.
#         THIS is what HubSpot's geekly_unit_id stores.
#
# So each unit's detail record is fetched from Propertyware
# and its idNumber is resolved before any HubSpot matching.
#
# Logic:
#
#   Propertyware Draft Leases
#          |
#          | unitID (internal) + moveInDate
#          v
#   Group by internal Unit ID
#          |
#          v
#   Earliest future Move In Date per unit
#          |
#          v
#   Fetch Unit detail -> resolve idNumber
#          |
#          v
#   Existing HubSpot Unit (matched on geekly_unit_id)
#          |
#          v
#   Update upcoming_move_in_date
#
# A building with several units simply gets one date per unit.
# ============================================================


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# LOGGING
# ============================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("PW-Unit-HS")


# ============================================================
# PROPERTYWARE CONFIGURATION
# ============================================================

client_id = os.getenv("PW_CLIENT_ID")
client_secret = os.getenv("PW_CLIENT_SECRET")
system_id = os.getenv("PW_SYSTEM_ID")

base_url = "https://api.propertyware.com/pw/api/rest/v1"

headers = {
    "x-propertyware-client-id": client_id,
    "x-propertyware-client-secret": client_secret,
    "x-propertyware-system-id": system_id,
    "Content-Type": "application/json",
    "Accept": "application/json",
}


# ============================================================
# TIMEOUT / RETRY CONFIGURATION
# ============================================================

PW_CONNECT_TIMEOUT = int(os.getenv("PW_CONNECT_TIMEOUT", "30"))
PW_READ_TIMEOUT = int(os.getenv("PW_READ_TIMEOUT", "300"))

REQUEST_TIMEOUT = (PW_CONNECT_TIMEOUT, PW_READ_TIMEOUT)

MAX_RETRIES = int(os.getenv("PW_MAX_RETRIES", "4"))
RETRY_DELAY = float(os.getenv("PW_RETRY_DELAY", "3"))

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


# ============================================================
# HUBSPOT CONFIGURATION
# ============================================================

# HubSpot Unit custom object type ID.
HUBSPOT_OBJECT_TYPE = "2-11351438"

# Property used to match the Propertyware unit idNumber.
HUBSPOT_MATCH_PROPERTY = "geekly_unit_id"

# HubSpot date property that will be updated.
HUBSPOT_DATE_PROPERTY = "upcoming_move_in_date"

# Optional diagnostic properties. Leave blank to skip them.
# They are NOT required for the script to work.
HUBSPOT_SOURCE_LEASE_NAME_PROPERTY = os.getenv(
    "HUBSPOT_SOURCE_LEASE_NAME_PROPERTY", ""
).strip()

HUBSPOT_SOURCE_LEASE_ID_PROPERTY = os.getenv(
    "HUBSPOT_SOURCE_LEASE_ID_PROPERTY", ""
).strip()


# ============================================================
# HUBSPOT SETTINGS
# ============================================================

HUBSPOT_PAGE_SIZE = int(os.getenv("HUBSPOT_PAGE_SIZE", "100"))
HUBSPOT_WRITE_DELAY = float(os.getenv("HUBSPOT_WRITE_DELAY", "0.25"))

PRELOAD_HUBSPOT_INDEX = (
    os.getenv("PRELOAD_HUBSPOT_INDEX", "true").lower() == "true"
)

# If True:
#
#   A HubSpot Unit that currently has an upcoming_move_in_date
#   but no longer has a future Draft lease in Propertyware
#   -> its upcoming_move_in_date is cleared.
#
# Safeguards: the clear pass is SKIPPED if the Draft lease fetch
# was incomplete, if the HubSpot index was not preloaded, or if
# any unit ID failed to resolve (to avoid wiping valid dates
# because of a partial/failed API run).
CLEAR_DATE_IF_NO_DRAFT = (
    os.getenv("CLEAR_DATE_IF_NO_DRAFT", "false").lower() == "true"
)


# ============================================================
# PROPERTYWARE PAGE SETTINGS
# ============================================================

PW_PAGE_SIZE = int(os.getenv("PW_PAGE_SIZE", "100"))


# ============================================================
# GENERIC VALUE -> TEXT
# ============================================================

def value_to_text(value):
    if value is None:
        return ""

    if isinstance(value, (int, float, bool)):
        return str(value)

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, dict):
        parts = []

        for key, val in value.items():
            parts.append(str(key))

            nested = value_to_text(val)

            if nested:
                parts.append(nested)

        return " ".join(parts).strip()

    if isinstance(value, list):
        return " ".join(value_to_text(item) for item in value).strip()

    return str(value).strip()


# ============================================================
# DATE PARSER
# ============================================================

def parse_date(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    value = str(value).strip()

    if not value:
        return None

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        pass

    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %H:%M",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


# ============================================================
# PROPERTYWARE API
# ============================================================

def make_api_request(
    endpoint,
    method="GET",
    payload=None,
    params=None,
    label=None,
):
    method = method.upper()
    request_label = label or endpoint

    for attempt in range(1, MAX_RETRIES + 1):
        started = time.monotonic()

        try:
            logger.info(
                "PW API | %s | attempt %d/%d",
                request_label,
                attempt,
                MAX_RETRIES,
            )

            if method == "GET":
                response = requests.get(
                    endpoint,
                    headers=headers,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                )

            elif method == "POST":
                response = requests.post(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )

            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            elapsed = time.monotonic() - started

            if response.status_code in {200, 201, 202}:
                logger.info(
                    "PW API | OK | %s | %.2fs",
                    request_label,
                    elapsed,
                )

                if not response.text.strip():
                    return {}

                try:
                    return response.json()
                except ValueError:
                    logger.warning(
                        "PW API | successful response was not JSON | %s",
                        request_label,
                    )
                    return None

            if response.status_code == 404:
                logger.warning(
                    "PW API | 404 | %s | %.2fs",
                    request_label,
                    elapsed,
                )
                return None

            if response.status_code in RETRYABLE_STATUS_CODES:
                retry_after = response.headers.get("Retry-After")

                if retry_after:
                    try:
                        wait_time = float(retry_after)
                    except ValueError:
                        wait_time = RETRY_DELAY * attempt
                else:
                    wait_time = RETRY_DELAY * attempt

                logger.warning(
                    "PW API | HTTP %s | %s | %.2fs | retry %d/%d in %.1fs",
                    response.status_code,
                    request_label,
                    elapsed,
                    attempt,
                    MAX_RETRIES,
                    wait_time,
                )

                if attempt < MAX_RETRIES:
                    time.sleep(wait_time)
                    continue

                logger.error(
                    "PW API | final retry failed | %s",
                    request_label,
                )
                return None

            logger.error(
                "PW API | HTTP %s | %s | response=%s",
                response.status_code,
                request_label,
                response.text[:1000],
            )
            return None

        except requests.exceptions.Timeout as exc:
            elapsed = time.monotonic() - started

            logger.warning(
                "PW API | TIMEOUT | %s | %.2fs | attempt %d/%d | %s",
                request_label,
                elapsed,
                attempt,
                MAX_RETRIES,
                exc,
            )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                logger.error(
                    "PW API | timeout retries exhausted | %s",
                    request_label,
                )
                return None

        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ) as exc:
            elapsed = time.monotonic() - started

            logger.warning(
                "PW API | CONNECTION ERROR | %s | %.2fs | attempt %d/%d | %s",
                request_label,
                elapsed,
                attempt,
                MAX_RETRIES,
                exc,
            )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                logger.error(
                    "PW API | connection retries exhausted | %s",
                    request_label,
                )
                return None

        except Exception:
            logger.exception(
                "PW API | unexpected error | %s",
                request_label,
            )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                return None

    return None


# ============================================================
# EXTRACT LIST FROM PROPERTYWARE RESPONSE
# ============================================================

def extract_list_from_response(response, possible_keys):
    if response is None:
        return []

    if isinstance(response, list):
        return response

    if isinstance(response, dict):
        for key in possible_keys:
            value = response.get(key)

            if isinstance(value, list):
                return value

    return []


# ============================================================
# FETCH PROPERTYWARE DRAFT LEASES
# ============================================================
#
# Returns (leases, complete).
#
# `complete` is False if a page request failed part-way, so
# callers know the data may be partial (important for the
# optional clear-stale-dates pass).
# ============================================================

def fetch_draft_leases():
    endpoint = f"{base_url}/leases"

    all_leases = []
    offset = 0
    complete = True

    logger.info("PW DRAFT LEASES | fetching Propertyware Draft leases...")

    while True:
        params = {
            "limit": PW_PAGE_SIZE,
            "offset": offset,
            "status": "Draft",
        }

        response = make_api_request(
            endpoint,
            params=params,
            label=f"draft leases offset={offset}",
        )

        if response is None:
            logger.error(
                "PW DRAFT LEASES | request failed at offset=%d",
                offset,
            )
            complete = False
            break

        page_leases = extract_list_from_response(
            response,
            ["leases", "results", "data", "items"],
        )

        if not page_leases:
            logger.info(
                "PW DRAFT LEASES | no more records at offset=%d",
                offset,
            )
            break

        all_leases.extend(page_leases)

        logger.info(
            "PW DRAFT LEASES | offset=%d | received=%d | total=%d",
            offset,
            len(page_leases),
            len(all_leases),
        )

        if len(page_leases) < PW_PAGE_SIZE:
            break

        offset += PW_PAGE_SIZE

        time.sleep(0.5)

    logger.info("PW DRAFT LEASES | fetched=%d", len(all_leases))

    return all_leases, complete


# ============================================================
# GET UNIT ID FROM LEASE (internal Propertyware ID)
# ============================================================

def get_lease_unit_id(lease):
    possible_keys = [
        "unitID",
        "unitId",
        "unit_id",
        "unit",
    ]

    for key in possible_keys:
        value = lease.get(key)

        if value is None:
            continue

        if isinstance(value, dict):
            for nested_key in ["id", "ID", "unitID", "unitId"]:
                nested_value = value.get(nested_key)

                if nested_value is not None:
                    text = value_to_text(nested_value).strip()

                    if text:
                        return text

        text = value_to_text(value).strip()

        if text:
            return text

    return ""


# ============================================================
# GET LEASE MOVE-IN DATE
# ============================================================

def get_lease_move_in_date(lease):
    possible_keys = [
        "moveInDate",
        "moveinDate",
        "move_in_date",
    ]

    for key in possible_keys:
        if key not in lease:
            continue

        parsed = parse_date(lease.get(key))

        if parsed:
            return parsed

    return None


# ============================================================
# GROUP DRAFT LEASES BY UNIT (internal PW unit ID)
# ============================================================

def build_next_move_in_index(draft_leases):
    today = datetime.now().date()

    unit_dates = defaultdict(list)

    ignored_no_unit = 0
    ignored_no_date = 0
    ignored_past_date = 0

    for lease in draft_leases:
        unit_id = get_lease_unit_id(lease)

        if not unit_id:
            ignored_no_unit += 1

            logger.warning(
                "PW DRAFT | lease has no unit ID | lease=%s | id=%s",
                value_to_text(lease.get("leaseName")),
                value_to_text(lease.get("id")),
            )

            continue

        move_in_date = get_lease_move_in_date(lease)

        if not move_in_date:
            ignored_no_date += 1

            logger.debug(
                "PW DRAFT | no move-in date | unit=%s | lease=%s",
                unit_id,
                value_to_text(lease.get("leaseName")),
            )

            continue

        if move_in_date < today:
            ignored_past_date += 1

            logger.debug(
                "PW DRAFT | past move-in date ignored | unit=%s | "
                "date=%s | lease=%s",
                unit_id,
                move_in_date,
                value_to_text(lease.get("leaseName")),
            )

            continue

        unit_dates[unit_id].append(
            {
                "date": move_in_date,
                "lease_id": value_to_text(lease.get("id")).strip(),
                "lease_number": value_to_text(lease.get("idNumber")).strip(),
                "lease_name": value_to_text(lease.get("leaseName")).strip(),
            }
        )

    next_move_ins = {}

    for unit_id, leases in unit_dates.items():
        leases.sort(key=lambda item: item["date"])
        next_move_ins[unit_id] = leases[0]

    logger.info(
        "PW MOVE-IN INDEX | units=%d | ignored_no_unit=%d | "
        "ignored_no_date=%d | ignored_past_date=%d",
        len(next_move_ins),
        ignored_no_unit,
        ignored_no_date,
        ignored_past_date,
    )

    for unit_id, data in next_move_ins.items():
        logger.info(
            "PW NEXT MOVE-IN | unit(internal)=%s | date=%s | "
            "lease=%s | lease_id=%s",
            unit_id,
            data["date"],
            data["lease_name"] or "(no lease name)",
            data["lease_id"] or "(no lease ID)",
        )

    return next_move_ins


# ============================================================
# FETCH UNIT DETAILS (to resolve idNumber)
# ============================================================
#
# lease["unitID"] is Propertyware's INTERNAL unit object ID.
# HubSpot's geekly_unit_id stores the unit's idNumber, so the
# full unit record is fetched and its idNumber extracted.
#
# Several likely field names are tried since the exact schema
# has not been confirmed. Once you have logged one real
# /units/{id} response, trim this list to the field that
# actually matches HubSpot.
# ============================================================

def fetch_unit_details(internal_unit_id):
    endpoint = f"{base_url}/units/{internal_unit_id}"

    return make_api_request(
        endpoint,
        label=f"unit-details unit={internal_unit_id}",
    )


def get_unit_id_number(unit_detail):
    if not isinstance(unit_detail, dict):
        return ""

    possible_keys = [
        "idNumber",
        "unitNumber",
        "unitIdNumber",
        "number",
    ]

    for key in possible_keys:
        text = value_to_text(unit_detail.get(key)).strip()

        if text:
            return text

    return ""


def resolve_unit_id_numbers(next_move_ins):
    """
    For each internal unit ID found on Draft leases, fetch the
    unit's detail record and resolve its idNumber.

    Returns (resolved, unresolved_count), where `resolved` is
    keyed by idNumber (the value that matches HubSpot's
    geekly_unit_id).
    """

    resolved = {}
    unresolved_count = 0

    for internal_unit_id, move_in_data in next_move_ins.items():
        detail = fetch_unit_details(internal_unit_id)

        id_number = get_unit_id_number(detail)

        if not id_number:
            unresolved_count += 1

            logger.warning(
                "PW UNIT | could not resolve idNumber for internal "
                "unit ID=%s | HubSpot update will be SKIPPED for "
                "this unit",
                internal_unit_id,
            )

            continue

        logger.info(
            "PW UNIT | internal ID=%s -> idNumber=%s",
            internal_unit_id,
            id_number,
        )

        move_in_data = dict(move_in_data)
        move_in_data["internal_unit_id"] = internal_unit_id

        if id_number in resolved:
            # Two internal IDs resolving to the same idNumber:
            # keep the earliest move-in date.
            existing = resolved[id_number]

            logger.warning(
                "PW UNIT | idNumber=%s resolved from multiple "
                "internal IDs (%s, %s) | keeping earliest date",
                id_number,
                existing.get("internal_unit_id"),
                internal_unit_id,
            )

            if move_in_data["date"] >= existing["date"]:
                continue

        resolved[id_number] = move_in_data

    logger.info(
        "PW UNIT RESOLVE | resolved=%d | unresolved=%d",
        len(resolved),
        unresolved_count,
    )

    return resolved, unresolved_count


# ============================================================
# HUBSPOT CLIENT
# ============================================================

def get_hubspot_client():
    token = os.getenv("HUBSPOT_ACCESS_TOKEN")

    if not token:
        raise EnvironmentError(
            "HUBSPOT_ACCESS_TOKEN environment variable not set"
        )

    return HubSpot(access_token=token)


# ============================================================
# HUBSPOT RETRY
# ============================================================

def hubspot_retry_call(operation_name, func):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            started = time.monotonic()

            result = func()

            elapsed = time.monotonic() - started

            logger.debug(
                "HS API | OK | %s | %.2fs",
                operation_name,
                elapsed,
            )

            return result

        except ApiException as exc:
            status_code = getattr(exc, "status", None)

            logger.warning(
                "HS API | %s | attempt %d/%d | status=%s | %s",
                operation_name,
                attempt,
                MAX_RETRIES,
                status_code,
                str(exc)[:1000],
            )

            if (
                status_code in RETRYABLE_STATUS_CODES
                and attempt < MAX_RETRIES
            ):
                time.sleep(RETRY_DELAY * attempt)
                continue

            return None

        except Exception:
            logger.exception(
                "HS API | unexpected error | %s",
                operation_name,
            )
            return None

    return None


# ============================================================
# LOAD HUBSPOT UNITS
# ============================================================

def load_hubspot_unit_index(client):
    index = defaultdict(list)

    after = None
    page_count = 0
    loaded_records = 0
    ignored_blank_ids = 0

    logger.info(
        "HS INDEX | loading existing Units by %s...",
        HUBSPOT_MATCH_PROPERTY,
    )

    while True:

        def do_get_page():
            kwargs = {
                "object_type": HUBSPOT_OBJECT_TYPE,
                "limit": HUBSPOT_PAGE_SIZE,
                "properties": [
                    HUBSPOT_MATCH_PROPERTY,
                    HUBSPOT_DATE_PROPERTY,
                ],
            }

            if after is not None:
                kwargs["after"] = after

            return client.crm.objects.basic_api.get_page(**kwargs)

        response = hubspot_retry_call(
            f"load HubSpot Unit page {page_count + 1}",
            do_get_page,
        )

        if response is None:
            logger.error("HS INDEX | unable to load HubSpot Unit page.")
            break

        page_count += 1

        results = response.results or []

        for record in results:
            properties = record.properties or {}

            unit_id = value_to_text(
                properties.get(HUBSPOT_MATCH_PROPERTY)
            ).strip()

            if not unit_id:
                ignored_blank_ids += 1
                continue

            index[unit_id].append(
                {
                    "id": str(record.id),
                    "unit_id": unit_id,
                    "current_date": parse_date(
                        properties.get(HUBSPOT_DATE_PROPERTY)
                    ),
                }
            )

            loaded_records += 1

        paging = getattr(response, "paging", None)

        next_page = getattr(paging, "next", None) if paging else None

        if not next_page or not getattr(next_page, "after", None):
            break

        after = next_page.after

    duplicate_groups = {
        unit_id: records
        for unit_id, records in index.items()
        if len(records) > 1
    }

    logger.info(
        "HS INDEX | pages=%d | valid Units=%d | unique %s values=%d | "
        "blank IDs ignored=%d",
        page_count,
        loaded_records,
        HUBSPOT_MATCH_PROPERTY,
        len(index),
        ignored_blank_ids,
    )

    if duplicate_groups:
        logger.warning(
            "HS INDEX | FOUND %d duplicate %s groups",
            len(duplicate_groups),
            HUBSPOT_MATCH_PROPERTY,
        )

        for unit_id, records in duplicate_groups.items():
            logger.warning(
                "HS DUPLICATE | %s=%s | HubSpot IDs=%s",
                HUBSPOT_MATCH_PROPERTY,
                unit_id,
                ", ".join(record["id"] for record in records),
            )

    return dict(index)


# ============================================================
# HUBSPOT SEARCH FALLBACK
# ============================================================

def search_hubspot_unit_by_id(client, unit_id):
    unit_id = value_to_text(unit_id).strip()

    if not unit_id:
        return []

    try:
        filter_obj = Filter(
            value=unit_id,
            property_name=HUBSPOT_MATCH_PROPERTY,
            operator="EQ",
        )

        search_request = PublicObjectSearchRequest(
            filter_groups=[FilterGroup(filters=[filter_obj])],
            limit=100,
            properties=[
                HUBSPOT_MATCH_PROPERTY,
                HUBSPOT_DATE_PROPERTY,
            ],
        )

        result = hubspot_retry_call(
            f"search {HUBSPOT_MATCH_PROPERTY}={unit_id}",
            lambda: client.crm.objects.search_api.do_search(
                object_type=HUBSPOT_OBJECT_TYPE,
                public_object_search_request=search_request,
            ),
        )

        if result and result.results:
            matches = []

            for record in result.results:
                properties = record.properties or {}

                matches.append(
                    {
                        "id": str(record.id),
                        "unit_id": unit_id,
                        "current_date": parse_date(
                            properties.get(HUBSPOT_DATE_PROPERTY)
                        ),
                    }
                )

            return matches

    except Exception:
        logger.exception(
            "HS SEARCH | error for %s=%s",
            HUBSPOT_MATCH_PROPERTY,
            unit_id,
        )

    return []


# ============================================================
# UPDATE HUBSPOT UNIT
# ============================================================
#
# `unit_id` here is the PW idNumber (the resolved,
# HubSpot-matching identifier), NOT the internal unitID from
# the lease. `next_move_in` still carries `internal_unit_id`
# for logging/traceability.
# ============================================================

def hubspot_update_unit(
    client,
    hubspot_index,
    unit_id,
    next_move_in,
):
    unit_id = value_to_text(unit_id).strip()

    internal_unit_id = value_to_text(
        (next_move_in or {}).get("internal_unit_id")
    ).strip()

    if not unit_id:
        return {
            "status": "skipped",
            "reason": "missing_unit_id",
        }

    matches = hubspot_index.get(unit_id, [])

    if not matches:
        matches = search_hubspot_unit_by_id(client, unit_id)

        if matches:
            hubspot_index[unit_id] = matches

    if not matches:
        logger.warning(
            "HS SKIPPED | UNIT NOT FOUND | PW Unit idNumber=%s | "
            "PW internal unitID=%s | NO HUBSPOT UNIT WILL BE CREATED",
            unit_id,
            internal_unit_id or "(unknown)",
        )

        return {
            "status": "skipped",
            "reason": "hubspot_unit_not_found",
            "unit_id": unit_id,
        }

    if len(matches) > 1:
        logger.warning(
            "HS DUPLICATE UNIT | %s=%s | found %d records | IDs=%s",
            HUBSPOT_MATCH_PROPERTY,
            unit_id,
            len(matches),
            ", ".join(str(record["id"]) for record in matches),
        )

    canonical = matches[0]

    hubspot_id = str(canonical["id"])

    new_date = next_move_in["date"]

    # Skip the write if HubSpot already has this date.
    if (
        canonical.get("current_date") == new_date
        and not HUBSPOT_SOURCE_LEASE_NAME_PROPERTY
        and not HUBSPOT_SOURCE_LEASE_ID_PROPERTY
    ):
        logger.info(
            "HS NO CHANGE | Unit idNumber=%s | HS ID=%s | "
            "already %s",
            unit_id,
            hubspot_id,
            new_date,
        )

        return {
            "status": "no_change",
            "unit_id": unit_id,
            "hubspot_id": hubspot_id,
        }

    properties = {
        HUBSPOT_DATE_PROPERTY: new_date.strftime("%Y-%m-%d"),
    }

    if HUBSPOT_SOURCE_LEASE_NAME_PROPERTY:
        properties[HUBSPOT_SOURCE_LEASE_NAME_PROPERTY] = (
            next_move_in["lease_name"]
        )

    if HUBSPOT_SOURCE_LEASE_ID_PROPERTY:
        properties[HUBSPOT_SOURCE_LEASE_ID_PROPERTY] = (
            next_move_in["lease_id"]
        )

    logger.info(
        "HS UPDATE | PW Unit idNumber=%s | PW internal unitID=%s | "
        "HS Unit=%s | upcoming_move_in=%s | source_lease=%s",
        unit_id,
        internal_unit_id or "(unknown)",
        hubspot_id,
        new_date,
        next_move_in["lease_name"] or "(none)",
    )

    obj_input = SimplePublicObjectInput(properties=properties)

    result = hubspot_retry_call(
        f"update HubSpot Unit {hubspot_id}",
        lambda: client.crm.objects.basic_api.update(
            object_type=HUBSPOT_OBJECT_TYPE,
            object_id=hubspot_id,
            simple_public_object_input=obj_input,
        ),
    )

    if result is not None:
        logger.info(
            "HS UPDATED | Unit idNumber=%s | HS ID=%s | "
            "upcoming_move_in=%s",
            unit_id,
            hubspot_id,
            new_date,
        )

        return {
            "status": "updated",
            "unit_id": unit_id,
            "hubspot_id": hubspot_id,
            "upcoming_move_in": new_date.isoformat(),
            "duplicate_count": max(0, len(matches) - 1),
        }

    logger.error(
        "HS FAILED | Unit idNumber=%s | HS ID=%s",
        unit_id,
        hubspot_id,
    )

    return {
        "status": "failed",
        "unit_id": unit_id,
        "hubspot_id": hubspot_id,
    }


# ============================================================
# CLEAR STALE DATES (optional)
# ============================================================
#
# Clears upcoming_move_in_date on HubSpot Units that have a date
# set but no longer have a future Draft lease in Propertyware.
# Only runs when CLEAR_DATE_IF_NO_DRAFT=true and all safeguards
# pass (see main()).
# ============================================================

def clear_stale_dates(client, hubspot_index, active_unit_ids):
    cleared = 0
    failed = 0

    for unit_id, records in hubspot_index.items():
        if unit_id in active_unit_ids:
            continue

        for record in records:
            if not record.get("current_date"):
                continue

            hubspot_id = str(record["id"])

            logger.info(
                "HS CLEAR | Unit idNumber=%s | HS ID=%s | "
                "removing stale date %s",
                unit_id,
                hubspot_id,
                record["current_date"],
            )

            obj_input = SimplePublicObjectInput(
                properties={HUBSPOT_DATE_PROPERTY: ""}
            )

            result = hubspot_retry_call(
                f"clear HubSpot Unit {hubspot_id}",
                lambda hid=hubspot_id, inp=obj_input: (
                    client.crm.objects.basic_api.update(
                        object_type=HUBSPOT_OBJECT_TYPE,
                        object_id=hid,
                        simple_public_object_input=inp,
                    )
                ),
            )

            if result is not None:
                cleared += 1
            else:
                failed += 1

            if HUBSPOT_WRITE_DELAY > 0:
                time.sleep(HUBSPOT_WRITE_DELAY)

    return cleared, failed


# ============================================================
# SAVE JSON
# ============================================================

def save_json(next_move_ins):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    filename = f"unit_upcoming_move_in_{timestamp}.json"

    output = {}

    for unit_id, data in next_move_ins.items():
        output[unit_id] = {
            "moveInDate": data["date"].isoformat(),
            "leaseId": data["lease_id"],
            "leaseNumber": data["lease_number"],
            "leaseName": data["lease_name"],
            "internalUnitId": data.get("internal_unit_id", ""),
        }

    with open(filename, "w", encoding="utf-8") as file_handle:
        json.dump(
            output,
            file_handle,
            indent=2,
            default=str,
        )

    logger.info("OUTPUT | JSON saved: %s", filename)

    return filename


# ============================================================
# PUSH TO HUBSPOT
# ============================================================

def push_to_hubspot(
    next_move_ins,
    hubspot_client,
    run_clear_pass=False,
):
    summary = {
        "candidates": len(next_move_ins),
        "updated": 0,
        "failed": 0,
        "skipped_not_found": 0,
        "no_change": 0,
        "duplicates_detected": 0,
        "cleared": 0,
        "clear_failed": 0,
    }

    hubspot_index = {}

    if PRELOAD_HUBSPOT_INDEX:
        hubspot_index = load_hubspot_unit_index(hubspot_client)

    logger.info("HS SYNC | starting UPDATE-ONLY Unit push")

    for index, item in enumerate(next_move_ins.items(), start=1):
        unit_id, next_move_in = item

        logger.info(
            "HS PUSH | %d/%d | PW Unit idNumber=%s",
            index,
            len(next_move_ins),
            unit_id,
        )

        result = hubspot_update_unit(
            hubspot_client,
            hubspot_index,
            unit_id,
            next_move_in,
        )

        status = result.get("status")

        if status == "updated":
            summary["updated"] += 1
            summary["duplicates_detected"] += result.get(
                "duplicate_count",
                0,
            )

        elif status == "skipped":
            summary["skipped_not_found"] += 1

        elif status == "no_change":
            summary["no_change"] += 1

        else:
            summary["failed"] += 1

        # Only sleep after actual writes.
        if status in {"updated", "failed"} and HUBSPOT_WRITE_DELAY > 0:
            time.sleep(HUBSPOT_WRITE_DELAY)

    if run_clear_pass:
        if not hubspot_index:
            logger.warning(
                "HS CLEAR | skipped: HubSpot index not loaded "
                "(set PRELOAD_HUBSPOT_INDEX=true)"
            )
        else:
            cleared, clear_failed = clear_stale_dates(
                hubspot_client,
                hubspot_index,
                set(next_move_ins.keys()),
            )

            summary["cleared"] = cleared
            summary["clear_failed"] = clear_failed

    return summary


# ============================================================
# PRINT SUMMARY
# ============================================================

def print_sync_summary(summary, json_filename):
    logger.info("")
    logger.info("=" * 80)
    logger.info("UNIT MOVE-IN SYNC COMPLETE")
    logger.info("=" * 80)

    logger.info(
        "Propertyware units with future Draft move-ins: %d",
        summary["candidates"],
    )

    logger.info("HubSpot Units UPDATED: %d", summary["updated"])

    logger.info(
        "HubSpot Units SKIPPED / NOT FOUND: %d",
        summary["skipped_not_found"],
    )

    logger.info("HubSpot Units FAILED: %d", summary["failed"])

    logger.info("HubSpot Units NO CHANGE: %d", summary["no_change"])

    logger.info(
        "Duplicate HubSpot Units detected: %d",
        summary["duplicates_detected"],
    )

    if CLEAR_DATE_IF_NO_DRAFT:
        logger.info(
            "HubSpot Units CLEARED (stale date): %d",
            summary["cleared"],
        )
        logger.info(
            "HubSpot Units CLEAR FAILED: %d",
            summary["clear_failed"],
        )

    logger.info("IMPORTANT: This run CREATED 0 HubSpot Units.")

    logger.info(
        "IMPORTANT: Draft Propertyware leases were NOT created in HubSpot."
    )

    logger.info("JSON comparison file: %s", json_filename)

    logger.info("=" * 80)

    if summary["failed"] == 0 and summary["clear_failed"] == 0:
        logger.info("SUCCESS | Unit sync completed.")
    else:
        logger.warning(
            "WARNING | %d Unit updates failed, %d clears failed.",
            summary["failed"],
            summary["clear_failed"],
        )


# ============================================================
# MAIN
# ============================================================

def main():
    logger.info("=" * 80)
    logger.info("PROPERTYWARE -> HUBSPOT UNIT UPCOMING MOVE-IN SYNC")
    logger.info("=" * 80)

    logger.info("Mode: UPDATE-ONLY - NO HUBSPOT UNIT CREATION")

    logger.info(
        "HubSpot Unit object type: %s",
        HUBSPOT_OBJECT_TYPE,
    )

    logger.info(
        "HubSpot matching property: %s (matched against PW Unit "
        "idNumber, NOT the internal unitID)",
        HUBSPOT_MATCH_PROPERTY,
    )

    logger.info("HubSpot date property: %s", HUBSPOT_DATE_PROPERTY)

    logger.info(
        "Clear date when no Draft lease: %s",
        CLEAR_DATE_IF_NO_DRAFT,
    )

    missing = []

    if not client_id:
        missing.append("PW_CLIENT_ID")

    if not client_secret:
        missing.append("PW_CLIENT_SECRET")

    if not system_id:
        missing.append("PW_SYSTEM_ID")

    if missing:
        raise EnvironmentError(
            "Missing environment variables: " + ", ".join(missing)
        )

    if not os.getenv("HUBSPOT_ACCESS_TOKEN"):
        raise EnvironmentError(
            "Missing environment variable: HUBSPOT_ACCESS_TOKEN"
        )

    # ========================================================
    # PROPERTYWARE CONNECTION TEST
    # ========================================================

    logger.info("PW TEST | testing Propertyware API...")

    test_result = make_api_request(
        f"{base_url}/leases",
        params={"limit": 1, "offset": 0},
        label="Propertyware connection test",
    )

    if test_result is None:
        raise RuntimeError("Propertyware API connection failed.")

    logger.info("PW TEST | Propertyware connection successful.")

    # ========================================================
    # FETCH DRAFT LEASES
    # ========================================================

    draft_leases, fetch_complete = fetch_draft_leases()

    if not draft_leases:
        logger.info("No Draft Propertyware leases found.")
        return

    # One-time diagnostic: log a sample lease so the real field
    # names (unitID, moveInDate, ...) can be confirmed.
    if LOG_LEVEL == "DEBUG":
        logger.debug(
            "PW SAMPLE LEASE | %s",
            json.dumps(draft_leases[0], indent=2, default=str),
        )

    # ========================================================
    # BUILD NEXT MOVE-IN INDEX (keyed by internal unit ID)
    # ========================================================

    next_move_ins_internal = build_next_move_in_index(draft_leases)

    if not next_move_ins_internal:
        logger.info("No future Draft move-in dates found.")
        return

    # ========================================================
    # RESOLVE idNumber FOR EACH UNIT
    # (HubSpot matches on idNumber, not the internal unitID)
    # ========================================================

    next_move_ins, unresolved_count = resolve_unit_id_numbers(
        next_move_ins_internal
    )

    if not next_move_ins:
        logger.warning(
            "No unit idNumbers could be resolved -- nothing to push "
            "to HubSpot. Check the get_unit_id_number() field-name "
            "list against your Propertyware /units/{id} response "
            "shape."
        )
        return

    # ========================================================
    # DECIDE WHETHER THE CLEAR PASS IS SAFE
    # ========================================================

    run_clear_pass = False

    if CLEAR_DATE_IF_NO_DRAFT:
        if not fetch_complete:
            logger.warning(
                "HS CLEAR | skipped: Draft lease fetch was incomplete"
            )
        elif unresolved_count > 0:
            logger.warning(
                "HS CLEAR | skipped: %d unit(s) failed to resolve",
                unresolved_count,
            )
        else:
            run_clear_pass = True

    # ========================================================
    # SAVE JSON
    # ========================================================

    json_filename = save_json(next_move_ins)

    # ========================================================
    # HUBSPOT
    # ========================================================

    logger.info("HS TEST | initializing HubSpot client...")

    hubspot_client = get_hubspot_client()

    logger.info("HS TEST | HubSpot client initialized.")

    # ========================================================
    # PUSH
    # ========================================================

    summary = push_to_hubspot(
        next_move_ins,
        hubspot_client,
        run_clear_pass=run_clear_pass,
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print_sync_summary(summary, json_filename)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        main()

    except KeyboardInterrupt:
        logger.warning("Script interrupted by user.")
        raise SystemExit(1)

    except Exception:
        logger.exception("FATAL ERROR")
        raise SystemExit(1)
