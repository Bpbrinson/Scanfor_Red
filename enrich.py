"""
enrich.py — Service Lookup Enrichment Module for Scanfor_Red
============================================================
Drop this file in the same directory as scanfor_red.py.

Usage in scanfor_red.py:
  1. At the top of the file, after imports:
       from enrich import load_service_lookup, enrich_with_service_info
       SERVICE_LOOKUP = load_service_lookup()

  2. Inside analyze(), after building each result dict:
       result = enrich_with_service_info(result, SERVICE_LOOKUP)
"""

import json
import os
import re


# ── Error meaning per hour-column number ───────────────────────────────────────
# Human-readable description of what a red cell in each column represents.
# Shared by the web frontend (Error column) and the Excel report.
ERROR_DESCRIPTIONS = {
    0:  "General ERROR",
    1:  "SSL algorithm blocked",
    2:  "Java exception",
    3:  "Null pointer exception",
    4:  "JDBC/database issue",
    5:  "SSL/TLS issue",
    6:  "Bind/port failure",
    7:  "General exception",
    8:  "Missing Java class",
    9:  "Algorithm negotiation failed",
    10: "Expired token",
    11: "SQL exception",
    12: "Credential issue",
    13: "Request timeout",
}


# ── Loader ─────────────────────────────────────────────────────────────────────

def load_service_lookup(lookup_path: str = None) -> dict:
    """
    Load service_lookup.json and return a dict keyed by service_pattern (lowercase).

    Falls back to an empty dict — with a console warning — if the file is not
    found, so the rest of the scanner keeps running without enrichment.

    Args:
        lookup_path: Optional explicit path. Defaults to service_lookup.json
                     in the same directory as this script.

    Returns:
        dict: { "fordserver": { ...entry... }, "bmwserver": { ...entry... }, ... }
    """
    if lookup_path is None:
        lookup_path = os.path.join(os.path.dirname(__file__), "service_lookup.json")

    if not os.path.exists(lookup_path):
        print(
            f"[WARN] service_lookup.json not found at '{lookup_path}'. "
            "Error enrichment will be skipped. Create the file to enable it."
        )
        return {}

    try:
        with open(lookup_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[ERROR] service_lookup.json is not valid JSON: {e}. Enrichment skipped.")
        return {}

    return {
        entry["service_pattern"].lower(): entry
        for entry in entries
        if entry.get("service_pattern") and entry["service_pattern"] != "TEMPLATE_COPY_ME"
    }


# ── Service name extraction ────────────────────────────────────────────────────

def extract_service_name(log_file_path: str) -> str:
    """
    Parse the service name out of a log file path.

    Strategy: the directory immediately containing the log file is the service
    name (log files are organised as .../service_name/logfile.date).

    Examples:
      /appl/labcore/log/cfrd/fordserver/fordserver-main.20260604  →  fordserver
      /appl/labcore/log/smslistener/smslistener-1.log             →  smslistener
      /appl/labcore/log/cfrd/regionserver/regionserver.20260604   →  regionserver

    Falls back to "unknown" rather than raising, so no result is ever dropped.
    """
    if not log_file_path:
        return "unknown"

    # Normalise separators and strip leading/trailing whitespace
    normalized = log_file_path.replace("\\", "/").strip()
    parts = [p for p in normalized.split("/") if p]

    # The log file is parts[-1]; its parent directory is the service name
    if len(parts) >= 2:
        return parts[-2].lower()

    # Edge case: flat path with no directory — strip the date suffix from filename
    if parts:
        stem = parts[0].split(".")[0]       # remove .20260604 or .log
        stem = re.sub(r"[-_](main|server|listener|daemon)$", "", stem)
        return stem.lower()

    return "unknown"


# ── Enrichment ────────────────────────────────────────────────────────────────

def enrich_with_service_info(result: dict, service_lookup: dict) -> dict:
    """
    Mutate a result dict in-place to add service_name and error_meaning fields.

    Always adds both keys — unmatched services receive a 'not found' payload
    rather than missing keys, so no downstream code needs to guard for absence.

    Args:
        result:         A single alert dict with at minimum a 'log_file' key.
        service_lookup: The dict returned by load_service_lookup().

    Returns:
        The same result dict, now containing 'service_name' and 'error_meaning'.
    """
    service_name = extract_service_name(result.get("log_file", ""))
    entry = service_lookup.get(service_name)

    result["service_name"] = service_name

    if entry:
        result["error_meaning"] = {
            "friendly_name":     entry.get("friendly_name", service_name),
            "error_description": entry.get("error_description", ""),
            "remediation_hint":  entry.get("remediation_hint", ""),
            "escalation_team":   entry.get("escalation_team", "Unknown"),
        }
    else:
        # Unmatched — return the raw token so no data is ever lost
        result["error_meaning"] = {
            "friendly_name":     service_name,
            "error_description": (
                f"No entry found for service '{service_name}' in service_lookup.json. "
                "Add a matching service_pattern entry to enable enrichment."
            ),
            "remediation_hint":  "Open service_lookup.json and add an entry for this service_pattern.",
            "escalation_team":   "Unknown — check service_lookup.json",
        }

    return result


# ── Quick self-test ───────────────────────────────────────────────────────────
# Run:  python enrich.py
# Expected: prints enriched result for fordserver and a fallback for unknownsvc

if __name__ == "__main__":
    import pprint

    lookup = load_service_lookup()
    print(f"Loaded {len(lookup)} service entries: {list(lookup.keys())}\n")

    test_cases = [
        {
            "log_file": "/appl/labcore/log/cfrd/fordserver/fordserver-main.20260604",
            "system": "labcore-ccgw-eastus2-prod-ford-vm-01",
            "column": 3,
            "count": 499,
        },
        {
            "log_file": "/appl/labcore/log/unknownsvc/unknownsvc-main.20260604",
            "system": "labcore-ccgw-eastus2-prod-test-vm-99",
            "column": 0,
            "count": 12,
        },
    ]

    for tc in test_cases:
        enriched = enrich_with_service_info(tc, lookup)
        pprint.pprint(enriched)
        print()
