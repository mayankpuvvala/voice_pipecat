"""Builds a caller contact list (name, phone, last call date, call count)
from fetch_calls() rows, for owners to export and follow up on — e.g. for
marketing. Exports every caller who's called in; consent for marketing use
of that contact info is the restaurant's responsibility, not enforced here.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")

_CSV_HEADER = ["Name", "Phone", "Last Call Date", "Total Calls", "Last Outcome"]


def _call_local_date(call: dict[str, Any]) -> str:
    ts = call.get("timestamp") or ""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ""
    return dt.astimezone(_IST).strftime("%Y-%m-%d")


def build_contact_rows(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per unique phone number, newest call first. `calls` is
    already newest-first (see sheets_reader.fetch_calls), so the first
    call seen per phone number is that contact's most recent one."""
    contacts: dict[str, dict[str, Any]] = {}
    for call in calls:
        phone = (call.get("caller_phone") or "").strip()
        if not phone:
            continue
        contact = contacts.setdefault(
            phone,
            {
                "name": call.get("caller_name") or "",
                "phone": phone,
                "last_call_date": _call_local_date(call),
                "total_calls": 0,
                "last_outcome": "Follow-up needed" if call.get("needs_followup") else "Resolved",
            },
        )
        contact["total_calls"] += 1
        if not contact["name"] and call.get("caller_name"):
            contact["name"] = call["caller_name"]
    return sorted(contacts.values(), key=lambda c: c["last_call_date"], reverse=True)


def contacts_csv(calls: list[dict[str, Any]]) -> str:
    rows = build_contact_rows(calls)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_HEADER)
    for row in rows:
        writer.writerow(
            [row["name"], row["phone"], row["last_call_date"], row["total_calls"], row["last_outcome"]]
        )
    return buf.getvalue()
