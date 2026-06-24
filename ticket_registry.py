"""
ticket_registry.py — Known-error ticket registry for Scanfor_Red
================================================================
Pre-fills the manual ticket columns (Ticket Created?, Ticket ID, Notes) for
recurring errors, so you don't have to re-enter them on every new report.

The registry is a JSON list in ticket_registry.json, one entry per known error:

    [
      {
        "system": "labcore-mxmcpiog01",   # host (omit or "*" = any host)
        "service": "listener",            # service name (required)
        "column": 11,                     # error column (omit or null = any column)
        "ticket_exists": "Yes",
        "ticket_id": "JIRA-1234",
        "ticket_notes": "Known SQL timeout — ticket open"
      }
    ]

Matching for an alert tries most-specific first:
    (system, service, column) -> (system, service, *) ->
    (*, service, column)      -> (*, service, *)

The web "Save tickets" action upserts entries here automatically, so triaging
one report teaches the registry for the future.
"""

import os
import json
import shutil
import threading

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ticket_registry.json")
_LOCK = threading.Lock()
_FIELDS = ("ticket_exists", "ticket_id", "ticket_notes")


def _key(system, service, column):
    return f"{system}||{service}||{column}"


def load_registry(path=None):
    """Return the registry as a list (empty list if missing/invalid)."""
    path = path or _PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def build_index(entries):
    """Build a lookup dict from registry entries (supports '*' wildcards)."""
    index = {}
    for e in entries:
        system = e.get("system") or "*"
        service = e.get("service") or ""
        col = e.get("column")
        col = "*" if col is None else col
        if not service:
            continue
        index[_key(system, service, col)] = {f: e.get(f, "") for f in _FIELDS}
    return index


def match(index, system, service, column):
    """Find ticket fields for an alert, most-specific match first."""
    for sys_k in (system, "*"):
        for col_k in (column, "*"):
            hit = index.get(_key(sys_k, service, col_k))
            if hit:
                return hit
    return None


def apply_to_alert(alert, index):
    """Fill an alert's empty ticket fields from a registry match (in place)."""
    hit = match(index, alert.get("system", ""), alert.get("service_name", ""),
                alert.get("column"))
    if hit:
        for f in _FIELDS:
            if not alert.get(f) and hit.get(f):
                alert[f] = hit[f]
    return alert


def delete_entry(system, service, column, path=None):
    """Remove one entry from the registry by its (system, service, column) key.

    Returns True if an entry was removed, False if none matched.
    """
    path = path or _PATH
    with _LOCK:
        entries = load_registry(path)
        sys_k = system or "*"
        col_k = "*" if column is None else column
        target = _key(sys_k, service, col_k)
        kept = [
            e for e in entries
            if _key(e.get("system") or "*", e.get("service") or "",
                    "*" if e.get("column") is None else e.get("column")) != target
        ]
        if len(kept) == len(entries):
            return False
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(kept, f, indent=2)
        shutil.move(tmp, path)
    return True


def upsert(items, path=None):
    """Insert/update/remove registry entries from saved ticket rows.

    items: dicts with system, service, column, ticket_exists, ticket_id,
    ticket_notes. An entry whose three fields are all empty is removed (so
    clearing a ticket on a report forgets it). Writes atomically.
    """
    path = path or _PATH
    with _LOCK:
        entries = load_registry(path)
        by_key = {
            _key(e.get("system", ""), e.get("service", ""), e.get("column")): e
            for e in entries
        }
        for it in items:
            system = it.get("system", "")
            service = it.get("service", "")
            column = it.get("column")
            if not service:
                continue
            vals = {f: (it.get(f) or "").strip() for f in _FIELDS}
            k = _key(system, service, column)
            if any(vals.values()):
                entry = by_key.get(k)
                if entry is None:
                    entry = {"system": system, "service": service, "column": column}
                    entries.append(entry)
                    by_key[k] = entry
                entry.update(vals)
            elif k in by_key:
                entries.remove(by_key.pop(k))

        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
        shutil.move(tmp, path)
    return len(entries)
