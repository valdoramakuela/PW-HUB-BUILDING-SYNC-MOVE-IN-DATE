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
# PROPERTYWARE -> HUBSPOT BUILDING NEXT MOVE-IN SYNC
# ============================================================
#
# SAFETY RULE:
#
# This script is UPDATE-ONLY in HubSpot.
#
# - It NEVER creates HubSpot Buildings.
# - It matches Propertyware building idNumber against:
#
#       HubSpot geekly_building_id
#
# - It updates:
#
#       next_move_in_date
#
# - Draft Propertyware leases are used only as source data.
# - Draft leases are NOT created in HubSpot.
#
# ------------------------------------------------------------
# IMPORTANT FIX:
#
# Propertyware exposes two different building identifiers,
# exactly like it does for leases:
#
#     lease["buildingID"]
#         Propertyware's INTERNAL building object ID
#         (e.g. 4801003522). This is what a lease record
#         hands you directly.
#
#     building["idNumber"]
#         The actual building number shown in the
#         Propertyware UI. THIS is what HubSpot's
#         geekly_building_id stores.
#
# The previous version of this script matched using the
# internal buildingID pulled straight off the lease, which is
# why every single Building lookup failed with
# "BUILDING NOT FOUND" even though the HubSpot index loaded
# thousands of valid records.
#
# This version fetches each candidate building's detail
# record from Propertyware and resolves its idNumber before
# doing any HubSpot matching -- mirroring exactly how the
# lease sync resolves lease["idNumber"] vs lease["id"].
# ------------------------------------------------------------
#
# Logic:
#
#   Propertyware Draft Leases
#          |
#          | buildingID (internal) + moveInDate
#          v
#   Group by internal Building ID
#          |
#          v
#   Earliest future Move In Date
#          |
#          v
#   Fetch Building detail -> resolve idNumber
#          |
#          v
#   Existing HubSpot Building (matched on idNumber)
#          |
#          v
#   Update next_move_in_date
#
# ============================================================


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# LOGGING
# ============================================================

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
).upper()

logging.basicConfig(
    level=getattr(
        logging,
        LOG_LEVEL,
        logging.INFO,
    ),
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(
    "PW-Building-HS"
)


# ============================================================
# PROPERTYWARE CONFIGURATION
# ============================================================

client_id = os.getenv(
    "PW_CLIENT_ID"
)

client_secret = os.getenv(
    "PW_CLIENT_SECRET"
)

system_id = os.getenv(
    "PW_SYSTEM_ID"
)

base_url = (
    "https://api.propertyware.com/"
    "pw/api/rest/v1"
)

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

PW_CONNECT_TIMEOUT = int(
    os.getenv(
        "PW_CONNECT_TIMEOUT",
        "30",
    )
)

PW_READ_TIMEOUT = int(
    os.getenv(
        "PW_READ_TIMEOUT",
        "300",
    )
)

REQUEST_TIMEOUT = (
    PW_CONNECT_TIMEOUT,
    PW_READ_TIMEOUT,
)

MAX_RETRIES = int(
    os.getenv(
        "PW_MAX_RETRIES",
        "4",
    )
)

RETRY_DELAY = float(
    os.getenv(
        "PW_RETRY_DELAY",
        "3",
    )
)

RETRYABLE_STATUS_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
}


# ============================================================
# HUBSPOT CONFIGURATION
# ============================================================

HUBSPOT_OBJECT_TYPE = "2-11351415"

# Property used to match Propertyware Building idNumber.
HUBSPOT_MATCH_PROPERTY = "geekly_building_id"

# HubSpot date property we will update.
HUBSPOT_DATE_PROPERTY = "next_move_in_date"

# Optional diagnostic properties.
#
# Leave these blank if you do not want to update them.
#
# They are NOT required for the script to work.
HUBSPOT_SOURCE_LEASE_NAME_PROPERTY = os.getenv(
    "HUBSPOT_SOURCE_LEASE_NAME_PROPERTY",
    "",
).strip()

HUBSPOT_SOURCE_LEASE_ID_PROPERTY = os.getenv(
    "HUBSPOT_SOURCE_LEASE_ID_PROPERTY",
    "",
).strip()


# ============================================================
# HUBSPOT SETTINGS
# ============================================================

HUBSPOT_PAGE_SIZE = int(
    os.getenv(
        "HUBSPOT_PAGE_SIZE",
        "100",
    )
)

HUBSPOT_WRITE_DELAY = float(
    os.getenv(
        "HUBSPOT_WRITE_DELAY",
        "0.25",
    )
)

PRELOAD_HUBSPOT_INDEX = (
    os.getenv(
        "PRELOAD_HUBSPOT_INDEX",
        "true",
    ).lower()
    == "true"
)

# If True:
#
#   Building currently has no future Draft lease
#       ->
#   next_move_in_date is cleared.
#
# This keeps HubSpot accurate when a Draft lease disappears
# or changes status.
CLEAR_DATE_IF_NO_DRAFT = (
    os.getenv(
        "CLEAR_DATE_IF_NO_DRAFT",
        "false",
    ).lower()
    == "true"
)


# ============================================================
# PROPERTYWARE PAGE SETTINGS
# ============================================================

PW_PAGE_SIZE = int(
    os.getenv(
        "PW_PAGE_SIZE",
        "100",
    )
)


# ============================================================
# GENERIC VALUE -> TEXT
# ============================================================

def value_to_text(value):
    if value is None:
        return ""

    if isinstance(
        value,
        (int, float, bool),
    ):
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
        return " ".join(
            value_to_text(item)
            for item in value
        ).strip()

    return str(value).strip()


# ============================================================
# DATE PARSER
# ============================================================

def parse_date(value):
    if value is None:
        return None

    if isinstance(
        value,
        datetime,
    ):
        return value.date()

    if isinstance(
        value,
        date,
    ):
        return value

    value = str(value).strip()

    if not value:
        return None

    try:
        iso_value = value.replace(
            "Z",
            "+00:00",
        )

        return datetime.fromisoformat(
            iso_value
        ).date()

    except (
        ValueError,
        TypeError,
    ):
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
            return datetime.strptime(
                value,
                fmt,
            ).date()

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

    request_label = (
        label
        or endpoint
    )

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):
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

                raise ValueError(
                    f"Unsupported HTTP method: {method}"
                )

            elapsed = (
                time.monotonic()
                - started
            )

            if response.status_code in {
                200,
                201,
                202,
            }:

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
                        "PW API | successful response "
                        "was not JSON | %s",
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

            if (
                response.status_code
                in RETRYABLE_STATUS_CODES
            ):

                retry_after = (
                    response.headers.get(
                        "Retry-After"
                    )
                )

                if retry_after:

                    try:
                        wait_time = float(
                            retry_after
                        )

                    except ValueError:
                        wait_time = (
                            RETRY_DELAY
                            * attempt
                        )

                else:
                    wait_time = (
                        RETRY_DELAY
                        * attempt
                    )

                logger.warning(
                    "PW API | HTTP %s | %s | "
                    "%.2fs | retry %d/%d "
                    "in %.1fs",
                    response.status_code,
                    request_label,
                    elapsed,
                    attempt,
                    MAX_RETRIES,
                    wait_time,
                )

                if attempt < MAX_RETRIES:

                    time.sleep(
                        wait_time
                    )

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

            elapsed = (
                time.monotonic()
                - started
            )

            logger.warning(
                "PW API | TIMEOUT | %s | "
                "%.2fs | attempt %d/%d | %s",
                request_label,
                elapsed,
                attempt,
                MAX_RETRIES,
                exc,
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

            else:

                logger.error(
                    "PW API | timeout retries "
                    "exhausted | %s",
                    request_label,
                )

                return None

        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ) as exc:

            elapsed = (
                time.monotonic()
                - started
            )

            logger.warning(
                "PW API | CONNECTION ERROR | "
                "%s | %.2fs | attempt %d/%d | %s",
                request_label,
                elapsed,
                attempt,
                MAX_RETRIES,
                exc,
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

            else:

                logger.error(
                    "PW API | connection retries "
                    "exhausted | %s",
                    request_label,
                )

                return None

        except Exception:

            logger.exception(
                "PW API | unexpected error | %s",
                request_label,
            )

            if attempt < MAX_RETRIES:

                time.sleep(
                    RETRY_DELAY * attempt
                )

            else:

                return None

    return None


# ============================================================
# EXTRACT LIST FROM PROPERTYWARE RESPONSE
# ============================================================

def extract_list_from_response(
    response,
    possible_keys,
):
    if response is None:
        return []

    if isinstance(
        response,
        list,
    ):
        return response

    if isinstance(
        response,
        dict,
    ):

        for key in possible_keys:

            value = response.get(
                key
            )

            if isinstance(
                value,
                list,
            ):
                return value

    return []


# ============================================================
# FETCH PROPERTYWARE DRAFT LEASES
# ============================================================

def fetch_draft_leases():
    endpoint = (
        f"{base_url}/leases"
    )

    all_leases = []
    offset = 0

    logger.info(
        "PW DRAFT LEASES | fetching "
        "Propertyware Draft leases..."
    )

    while True:

        params = {
            "limit": PW_PAGE_SIZE,
            "offset": offset,
            "status": "Draft",
        }

        response = make_api_request(
            endpoint,
            params=params,
            label=(
                f"draft leases "
                f"offset={offset}"
            ),
        )

        if response is None:

            logger.error(
                "PW DRAFT LEASES | "
                "request failed at offset=%d",
                offset,
            )

            break

        page_leases = (
            extract_list_from_response(
                response,
                [
                    "leases",
                    "results",
                    "data",
                    "items",
                ],
            )
        )

        if not page_leases:

            logger.info(
                "PW DRAFT LEASES | "
                "no more records at offset=%d",
                offset,
            )

            break

        all_leases.extend(
            page_leases
        )

        logger.info(
            "PW DRAFT LEASES | "
            "offset=%d | received=%d | "
            "total=%d",
            offset,
            len(page_leases),
            len(all_leases),
        )

        if len(page_leases) < PW_PAGE_SIZE:
            break

        offset += PW_PAGE_SIZE

        time.sleep(0.5)

    logger.info(
        "PW DRAFT LEASES | fetched=%d",
        len(all_leases),
    )

    return all_leases


# ============================================================
# GET BUILDING ID FROM LEASE (internal Propertyware ID)
# ============================================================

def get_lease_building_id(
    lease
):
    possible_keys = [
        "buildingID",
        "buildingId",
        "building_id",
        "building",
    ]

    for key in possible_keys:

        value = lease.get(
            key
        )

        if value is None:
            continue

        if isinstance(
            value,
            dict,
        ):

            for nested_key in [
                "id",
                "ID",
                "buildingID",
                "buildingId",
            ]:

                nested_value = (
                    value.get(
                        nested_key
                    )
                )

                if nested_value is not None:

                    text = value_to_text(
                        nested_value
                    ).strip()

                    if text:
                        return text

        text = value_to_text(
            value
        ).strip()

        if text:
            return text

    return ""


# ============================================================
# GET LEASE MOVE-IN DATE
# ============================================================

def get_lease_move_in_date(
    lease
):
    possible_keys = [
        "moveInDate",
        "moveinDate",
        "move_in_date",
    ]

    for key in possible_keys:

        if key not in lease:
            continue

        parsed = parse_date(
            lease.get(key)
        )

        if parsed:
            return parsed

    return None


# ============================================================
# GROUP DRAFT LEASES BY BUILDING (internal PW building ID)
# ============================================================

def build_next_move_in_index(
    draft_leases
):
    today = datetime.now().date()

    building_dates = defaultdict(
        list
    )

    ignored_no_building = 0
    ignored_no_date = 0
    ignored_past_date = 0

    for lease in draft_leases:

        building_id = (
            get_lease_building_id(
                lease
            )
        )

        if not building_id:

            ignored_no_building += 1

            logger.warning(
                "PW DRAFT | lease has no "
                "building ID | lease=%s | id=%s",
                value_to_text(
                    lease.get(
                        "leaseName"
                    )
                ),
                value_to_text(
                    lease.get("id")
                ),
            )

            continue

        move_in_date = (
            get_lease_move_in_date(
                lease
            )
        )

        if not move_in_date:

            ignored_no_date += 1

            logger.debug(
                "PW DRAFT | no move-in date | "
                "building=%s | lease=%s",
                building_id,
                value_to_text(
                    lease.get(
                        "leaseName"
                    )
                ),
            )

            continue

        if move_in_date < today:

            ignored_past_date += 1

            logger.debug(
                "PW DRAFT | past move-in date "
                "ignored | building=%s | "
                "date=%s | lease=%s",
                building_id,
                move_in_date,
                value_to_text(
                    lease.get(
                        "leaseName"
                    )
                ),
            )

            continue

        building_dates[
            building_id
        ].append(
            {
                "date": move_in_date,
                "lease_id": value_to_text(
                    lease.get("id")
                ).strip(),
                "lease_number": value_to_text(
                    lease.get(
                        "idNumber"
                    )
                ).strip(),
                "lease_name": value_to_text(
                    lease.get(
                        "leaseName"
                    )
                ).strip(),
            }
        )

    next_move_ins = {}

    for building_id, leases in (
        building_dates.items()
    ):

        leases.sort(
            key=lambda item: item["date"]
        )

        next_move_ins[
            building_id
        ] = leases[0]

    logger.info(
        "PW MOVE-IN INDEX | buildings=%d | "
        "ignored_no_building=%d | "
        "ignored_no_date=%d | "
        "ignored_past_date=%d",
        len(next_move_ins),
        ignored_no_building,
        ignored_no_date,
        ignored_past_date,
    )

    for building_id, data in (
        next_move_ins.items()
    ):

        logger.info(
            "PW NEXT MOVE-IN | "
            "building(internal)=%s | date=%s | "
            "lease=%s | lease_id=%s",
            building_id,
            data["date"],
            data["lease_name"]
            or "(no lease name)",
            data["lease_id"]
            or "(no lease ID)",
        )

    return next_move_ins


# ============================================================
# FETCH BUILDING DETAILS (to resolve idNumber)
# ============================================================
#
# THIS IS THE FIX.
#
# lease["buildingID"] is Propertyware's INTERNAL building
# object ID. HubSpot's geekly_building_id stores the
# building's idNumber (the human-facing building number),
# NOT this internal ID -- exactly like leases:
# lease["id"] (internal) vs lease["idNumber"] (matches
# HubSpot geekly_lease_id).
#
# This fetches the full building record and extracts its
# idNumber, trying several likely field names since we
# don't have a confirmed schema.
# ============================================================

def fetch_building_details(
    internal_building_id
):
    endpoint = (
        f"{base_url}/buildings/"
        f"{internal_building_id}"
    )

    return make_api_request(
        endpoint,
        label=(
            f"building-details "
            f"building={internal_building_id}"
        ),
    )


def get_building_id_number(
    building_detail
):
    """
    Resolve the Propertyware building "number" that HubSpot's
    geekly_building_id actually stores. Tries the most likely
    field names in order of confidence.
    """

    if not isinstance(
        building_detail,
        dict,
    ):
        return ""

    possible_keys = [
        "idNumber",
        "buildingNumber",
        "buildingIdNumber",
        "propertyIdNumber",
        "number",
    ]

    for key in possible_keys:

        value = building_detail.get(
            key
        )

        text = value_to_text(
            value
        ).strip()

        if text:
            return text

    return ""


def resolve_building_id_numbers(
    next_move_ins
):
    """
    For each internal building ID found on Draft leases,
    fetch the building's detail record and resolve its
    idNumber. Returns a new dict keyed by idNumber (the
    value that actually matches HubSpot's geekly_building_id),
    while retaining the internal ID and move-in data for
    logging/diagnostics.
    """

    resolved = {}

    unresolved_count = 0

    for (
        internal_building_id,
        move_in_data,
    ) in next_move_ins.items():

        detail = fetch_building_details(
            internal_building_id
        )

        id_number = (
            get_building_id_number(
                detail
            )
        )

        if not id_number:

            unresolved_count += 1

            logger.warning(
                "PW BUILDING | could not resolve "
                "idNumber for internal building "
                "ID=%s | HubSpot update will be "
                "SKIPPED for this building",
                internal_building_id,
            )

            continue

        logger.info(
            "PW BUILDING | internal ID=%s -> "
            "idNumber=%s",
            internal_building_id,
            id_number,
        )

        move_in_data = dict(
            move_in_data
        )

        move_in_data[
            "internal_building_id"
        ] = internal_building_id

        resolved[
            id_number
        ] = move_in_data

    logger.info(
        "PW BUILDING RESOLVE | resolved=%d | "
        "unresolved=%d",
        len(resolved),
        unresolved_count,
    )

    return resolved


# ============================================================
# HUBSPOT CLIENT
# ============================================================

def get_hubspot_client():

    token = os.getenv(
        "HUBSPOT_ACCESS_TOKEN"
    )

    if not token:

        raise EnvironmentError(
            "HUBSPOT_ACCESS_TOKEN "
            "environment variable not set"
        )

    return HubSpot(
        access_token=token
    )


# ============================================================
# HUBSPOT RETRY
# ============================================================

def hubspot_retry_call(
    operation_name,
    func,
):
    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            started = time.monotonic()

            result = func()

            elapsed = (
                time.monotonic()
                - started
            )

            logger.debug(
                "HS API | OK | %s | %.2fs",
                operation_name,
                elapsed,
            )

            return result

        except ApiException as exc:

            status_code = getattr(
                exc,
                "status",
                None,
            )

            logger.warning(
                "HS API | %s | attempt %d/%d | "
                "status=%s | %s",
                operation_name,
                attempt,
                MAX_RETRIES,
                status_code,
                str(exc)[:1000],
            )

            if (
                status_code
                in RETRYABLE_STATUS_CODES
                and attempt < MAX_RETRIES
            ):

                time.sleep(
                    RETRY_DELAY
                    * attempt
                )

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
# LOAD HUBSPOT BUILDINGS
# ============================================================

def load_hubspot_building_index(
    client
):
    index = defaultdict(
        list
    )

    after = None
    page_count = 0
    loaded_records = 0
    ignored_blank_ids = 0

    logger.info(
        "HS INDEX | loading existing "
        "Buildings by %s...",
        HUBSPOT_MATCH_PROPERTY,
    )

    while True:

        def do_get_page():

            kwargs = {
                "object_type":
                    HUBSPOT_OBJECT_TYPE,

                "limit":
                    HUBSPOT_PAGE_SIZE,

                "properties": [
                    HUBSPOT_MATCH_PROPERTY,
                ],
            }

            if after is not None:
                kwargs["after"] = after

            return (
                client
                .crm
                .objects
                .basic_api
                .get_page(
                    **kwargs
                )
            )

        response = hubspot_retry_call(
            (
                f"load HubSpot Building "
                f"page {page_count + 1}"
            ),
            do_get_page,
        )

        if response is None:

            logger.error(
                "HS INDEX | unable to load "
                "HubSpot Building page."
            )

            break

        page_count += 1

        results = (
            response.results
            or []
        )

        for record in results:

            properties = (
                record.properties
                or {}
            )

            building_id = (
                value_to_text(
                    properties.get(
                        HUBSPOT_MATCH_PROPERTY
                    )
                ).strip()
            )

            if not building_id:

                ignored_blank_ids += 1

                continue

            index[
                building_id
            ].append(
                {
                    "id": str(
                        record.id
                    ),
                    "building_id":
                        building_id,
                }
            )

            loaded_records += 1

        paging = getattr(
            response,
            "paging",
            None,
        )

        next_page = (
            getattr(
                paging,
                "next",
                None,
            )
            if paging
            else None
        )

        if (
            not next_page
            or not getattr(
                next_page,
                "after",
                None,
            )
        ):
            break

        after = next_page.after

    duplicate_groups = {
        building_id: records
        for (
            building_id,
            records
        ) in index.items()
        if len(records) > 1
    }

    logger.info(
        "HS INDEX | pages=%d | "
        "valid Buildings=%d | "
        "unique %s values=%d | "
        "blank IDs ignored=%d",
        page_count,
        loaded_records,
        HUBSPOT_MATCH_PROPERTY,
        len(index),
        ignored_blank_ids,
    )

    if duplicate_groups:

        logger.warning(
            "HS INDEX | FOUND %d duplicate "
            "%s groups",
            len(duplicate_groups),
            HUBSPOT_MATCH_PROPERTY,
        )

        for (
            building_id,
            records,
        ) in duplicate_groups.items():

            logger.warning(
                "HS DUPLICATE | %s=%s | "
                "HubSpot IDs=%s",
                HUBSPOT_MATCH_PROPERTY,
                building_id,
                ", ".join(
                    record["id"]
                    for record in records
                ),
            )

    return dict(index)


# ============================================================
# HUBSPOT SEARCH FALLBACK
# ============================================================

def search_hubspot_building_by_id(
    client,
    building_id,
):
    building_id = value_to_text(
        building_id
    ).strip()

    if not building_id:
        return []

    try:

        filter_obj = Filter(
            value=building_id,
            property_name=(
                HUBSPOT_MATCH_PROPERTY
            ),
            operator="EQ",
        )

        search_request = (
            PublicObjectSearchRequest(
                filter_groups=[
                    FilterGroup(
                        filters=[
                            filter_obj
                        ]
                    )
                ],
                limit=100,
                properties=[
                    HUBSPOT_MATCH_PROPERTY,
                ],
            )
        )

        result = (
            hubspot_retry_call(
                (
                    f"search "
                    f"{HUBSPOT_MATCH_PROPERTY}"
                    f"={building_id}"
                ),
                lambda: (
                    client
                    .crm
                    .objects
                    .search_api
                    .do_search(
                        object_type=(
                            HUBSPOT_OBJECT_TYPE
                        ),
                        public_object_search_request=(
                            search_request
                        ),
                    )
                ),
            )
        )

        if (
            result
            and result.results
        ):

            matches = []

            for record in (
                result.results
            ):

                matches.append(
                    {
                        "id": str(
                            record.id
                        ),
                        "building_id":
                            building_id,
                    }
                )

            return matches

    except Exception:

        logger.exception(
            "HS SEARCH | error for "
            "%s=%s",
            HUBSPOT_MATCH_PROPERTY,
            building_id,
        )

    return []


# ============================================================
# UPDATE HUBSPOT BUILDING
# ============================================================
#
# NOTE: `building_id` here is now the PW idNumber (the
# resolved, HubSpot-matching identifier), NOT the internal
# buildingID from the lease. `next_move_in` still carries
# `internal_building_id` for logging/traceability.
# ============================================================

def hubspot_update_building(
    client,
    hubspot_index,
    building_id,
    next_move_in,
):
    building_id = value_to_text(
        building_id
    ).strip()

    internal_building_id = (
        value_to_text(
            (next_move_in or {}).get(
                "internal_building_id"
            )
        ).strip()
    )

    if not building_id:

        return {
            "status": "skipped",
            "reason": "missing_building_id",
        }

    matches = (
        hubspot_index.get(
            building_id,
            [],
        )
    )

    if not matches:

        matches = (
            search_hubspot_building_by_id(
                client,
                building_id,
            )
        )

        if matches:

            hubspot_index[
                building_id
            ] = matches

    if not matches:

        logger.warning(
            "HS SKIPPED | BUILDING NOT FOUND | "
            "PW Building idNumber=%s | "
            "PW internal buildingID=%s | "
            "NO HUBSPOT BUILDING WILL BE CREATED",
            building_id,
            internal_building_id
            or "(unknown)",
        )

        return {
            "status": "skipped",
            "reason": "hubspot_building_not_found",
            "building_id": building_id,
        }

    if len(matches) > 1:

        logger.warning(
            "HS DUPLICATE BUILDING | "
            "%s=%s | found %d records | "
            "IDs=%s",
            HUBSPOT_MATCH_PROPERTY,
            building_id,
            len(matches),
            ", ".join(
                str(
                    record["id"]
                )
                for record in matches
            ),
        )

    canonical = matches[0]

    hubspot_id = str(
        canonical["id"]
    )

    properties = {}

    # --------------------------------------------------------
    # DATE
    # --------------------------------------------------------

    if next_move_in:

        properties[
            HUBSPOT_DATE_PROPERTY
        ] = next_move_in[
            "date"
        ].strftime(
            "%Y-%m-%d"
        )

    elif CLEAR_DATE_IF_NO_DRAFT:

        # Empty string clears a HubSpot property.
        properties[
            HUBSPOT_DATE_PROPERTY
        ] = ""

    else:

        logger.info(
            "HS NO CHANGE | Building idNumber=%s | "
            "no Draft future move-in",
            building_id,
        )

        return {
            "status": "no_change",
            "building_id": building_id,
            "hubspot_id": hubspot_id,
        }

    # --------------------------------------------------------
    # OPTIONAL SOURCE LEASE PROPERTIES
    # --------------------------------------------------------

    if (
        next_move_in
        and HUBSPOT_SOURCE_LEASE_NAME_PROPERTY
    ):

        properties[
            HUBSPOT_SOURCE_LEASE_NAME_PROPERTY
        ] = (
            next_move_in[
                "lease_name"
            ]
        )

    if (
        next_move_in
        and HUBSPOT_SOURCE_LEASE_ID_PROPERTY
    ):

        properties[
            HUBSPOT_SOURCE_LEASE_ID_PROPERTY
        ] = (
            next_move_in[
                "lease_id"
            ]
        )

    logger.info(
        "HS UPDATE | PW Building idNumber=%s | "
        "PW internal buildingID=%s | "
        "HS Building=%s | next_move_in=%s | "
        "source_lease=%s",
        building_id,
        internal_building_id
        or "(unknown)",
        hubspot_id,
        (
            next_move_in["date"]
            if next_move_in
            else "(clearing)"
        ),
        (
            next_move_in["lease_name"]
            if next_move_in
            else "(none)"
        ),
    )

    obj_input = (
        SimplePublicObjectInput(
            properties=properties
        )
    )

    result = hubspot_retry_call(
        (
            f"update HubSpot Building "
            f"{hubspot_id}"
        ),
        lambda: (
            client
            .crm
            .objects
            .basic_api
            .update(
                object_type=(
                    HUBSPOT_OBJECT_TYPE
                ),
                object_id=hubspot_id,
                simple_public_object_input=(
                    obj_input
                ),
            )
        ),
    )

    if result is not None:

        logger.info(
            "HS UPDATED | Building idNumber=%s | "
            "HS ID=%s | next_move_in=%s",
            building_id,
            hubspot_id,
            (
                next_move_in["date"]
                if next_move_in
                else "(cleared)"
            ),
        )

        return {
            "status": "updated",
            "building_id": building_id,
            "hubspot_id": hubspot_id,
            "next_move_in": (
                next_move_in["date"].isoformat()
                if next_move_in
                else ""
            ),
            "duplicate_count": max(
                0,
                len(matches) - 1,
            ),
        }

    logger.error(
        "HS FAILED | Building idNumber=%s | "
        "HS ID=%s",
        building_id,
        hubspot_id,
    )

    return {
        "status": "failed",
        "building_id": building_id,
        "hubspot_id": hubspot_id,
    }


# ============================================================
# SAVE JSON
# ============================================================

def save_json(
    next_move_ins
):
    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    filename = (
        "building_next_move_in_"
        f"{timestamp}.json"
    )

    output = {}

    for (
        building_id,
        data,
    ) in next_move_ins.items():

        output[
            building_id
        ] = {
            "moveInDate":
                data["date"].isoformat(),
            "leaseId":
                data["lease_id"],
            "leaseNumber":
                data["lease_number"],
            "leaseName":
                data["lease_name"],
            "internalBuildingId":
                data.get(
                    "internal_building_id",
                    "",
                ),
        }

    with open(
        filename,
        "w",
        encoding="utf-8",
    ) as file_handle:

        json.dump(
            output,
            file_handle,
            indent=2,
            default=str,
        )

    logger.info(
        "OUTPUT | JSON saved: %s",
        filename,
    )

    return filename


# ============================================================
# PUSH TO HUBSPOT
# ============================================================

def push_to_hubspot(
    next_move_ins,
    hubspot_client,
):
    summary = {
        "candidates":
            len(next_move_ins),

        "updated":
            0,

        "failed":
            0,

        "skipped_not_found":
            0,

        "no_change":
            0,

        "duplicates_detected":
            0,
    }

    hubspot_index = {}

    if PRELOAD_HUBSPOT_INDEX:

        hubspot_index = (
            load_hubspot_building_index(
                hubspot_client
            )
        )

    logger.info(
        "HS SYNC | starting "
        "UPDATE-ONLY Building push"
    )

    for (
        index,
        item,
    ) in enumerate(
        next_move_ins.items(),
        start=1,
    ):

        building_id, next_move_in = item

        logger.info(
            "HS PUSH | %d/%d | "
            "PW Building idNumber=%s",
            index,
            len(next_move_ins),
            building_id,
        )

        result = (
            hubspot_update_building(
                hubspot_client,
                hubspot_index,
                building_id,
                next_move_in,
            )
        )

        status = result.get(
            "status"
        )

        if status == "updated":

            summary[
                "updated"
            ] += 1

            summary[
                "duplicates_detected"
            ] += result.get(
                "duplicate_count",
                0,
            )

        elif status == "skipped":

            summary[
                "skipped_not_found"
            ] += 1

        elif status == "no_change":

            summary[
                "no_change"
            ] += 1

        else:

            summary[
                "failed"
            ] += 1

        if (
            HUBSPOT_WRITE_DELAY
            > 0
        ):

            time.sleep(
                HUBSPOT_WRITE_DELAY
            )

    return summary


# ============================================================
# PRINT SUMMARY
# ============================================================

def print_sync_summary(
    summary,
    json_filename,
):
    logger.info("")
    logger.info("=" * 80)
    logger.info(
        "BUILDING MOVE-IN SYNC COMPLETE"
    )
    logger.info("=" * 80)

    logger.info(
        "Propertyware buildings with "
        "future Draft move-ins: %d",
        summary["candidates"],
    )

    logger.info(
        "HubSpot Buildings UPDATED: %d",
        summary["updated"],
    )

    logger.info(
        "HubSpot Buildings SKIPPED / NOT FOUND: %d",
        summary["skipped_not_found"],
    )

    logger.info(
        "HubSpot Buildings FAILED: %d",
        summary["failed"],
    )

    logger.info(
        "HubSpot Buildings NO CHANGE: %d",
        summary["no_change"],
    )

    logger.info(
        "Duplicate HubSpot Buildings detected: %d",
        summary["duplicates_detected"],
    )

    logger.info(
        "IMPORTANT: This run CREATED 0 HubSpot Buildings."
    )

    logger.info(
        "IMPORTANT: Draft Propertyware leases "
        "were NOT created in HubSpot."
    )

    logger.info(
        "JSON comparison file: %s",
        json_filename,
    )

    logger.info("=" * 80)

    if summary["failed"] == 0:

        logger.info(
            "SUCCESS | Building sync completed."
        )

    else:

        logger.warning(
            "WARNING | %d Building updates failed.",
            summary["failed"],
        )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info("=" * 80)
    logger.info(
        "PROPERTYWARE -> HUBSPOT "
        "BUILDING NEXT MOVE-IN SYNC"
    )
    logger.info("=" * 80)

    logger.info(
        "Mode: UPDATE-ONLY - "
        "NO HUBSPOT BUILDING CREATION"
    )

    logger.info(
        "HubSpot Building object type: %s",
        HUBSPOT_OBJECT_TYPE,
    )

    logger.info(
        "HubSpot matching property: %s "
        "(matched against PW Building idNumber, "
        "NOT the internal buildingID)",
        HUBSPOT_MATCH_PROPERTY,
    )

    logger.info(
        "HubSpot date property: %s",
        HUBSPOT_DATE_PROPERTY,
    )

    logger.info(
        "Clear date when no Draft lease: %s",
        CLEAR_DATE_IF_NO_DRAFT,
    )

    missing = []

    if not client_id:
        missing.append(
            "PW_CLIENT_ID"
        )

    if not client_secret:
        missing.append(
            "PW_CLIENT_SECRET"
        )

    if not system_id:
        missing.append(
            "PW_SYSTEM_ID"
        )

    if missing:

        raise EnvironmentError(
            "Missing environment variables: "
            + ", ".join(missing)
        )

    if not os.getenv(
        "HUBSPOT_ACCESS_TOKEN"
    ):

        raise EnvironmentError(
            "Missing environment variable: "
            "HUBSPOT_ACCESS_TOKEN"
        )

    # ========================================================
    # PROPERTYWARE CONNECTION TEST
    # ========================================================

    logger.info(
        "PW TEST | testing Propertyware API..."
    )

    test_result = make_api_request(
        f"{base_url}/leases",
        params={
            "limit": 1,
            "offset": 0,
        },
        label=(
            "Propertyware connection test"
        ),
    )

    if test_result is None:

        raise RuntimeError(
            "Propertyware API connection failed."
        )

    logger.info(
        "PW TEST | Propertyware connection successful."
    )

    # ========================================================
    # FETCH DRAFT LEASES
    # ========================================================

    draft_leases = (
        fetch_draft_leases()
    )

    if not draft_leases:

        logger.info(
            "No Draft Propertyware leases found."
        )

        return

    # ========================================================
    # BUILD NEXT MOVE-IN INDEX (keyed by internal building ID)
    # ========================================================

    next_move_ins_internal = (
        build_next_move_in_index(
            draft_leases
        )
    )

    if not next_move_ins_internal:

        logger.info(
            "No future Draft move-in dates found."
        )

        return

    # ========================================================
    # RESOLVE idNumber FOR EACH BUILDING
    # (this is the fix -- HubSpot matches on idNumber, not
    # the internal buildingID)
    # ========================================================

    next_move_ins = (
        resolve_building_id_numbers(
            next_move_ins_internal
        )
    )

    if not next_move_ins:

        logger.warning(
            "No building idNumbers could be resolved -- "
            "nothing to push to HubSpot. Check the "
            "get_building_id_number() field-name list "
            "against your Propertyware /buildings/{id} "
            "response shape."
        )

        return

    # ========================================================
    # SAVE JSON
    # ========================================================

    json_filename = save_json(
        next_move_ins
    )

    # ========================================================
    # HUBSPOT
    # ========================================================

    logger.info(
        "HS TEST | initializing HubSpot client..."
    )

    hubspot_client = (
        get_hubspot_client()
    )

    logger.info(
        "HS TEST | HubSpot client initialized."
    )

    # ========================================================
    # PUSH
    # ========================================================

    summary = push_to_hubspot(
        next_move_ins,
        hubspot_client,
    )

    # ========================================================
    # SUMMARY
    # ========================================================

    print_sync_summary(
        summary,
        json_filename,
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        logger.warning(
            "Script interrupted by user."
        )

        raise SystemExit(1)

    except Exception:

        logger.exception(
            "FATAL ERROR"
        )

        raise SystemExit(1)
