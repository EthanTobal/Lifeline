"""LifeLine demo CUSTOMER POLICY RECORD lookup (used by the policy path).

Looks up an INDIVIDUAL customer's existing policy record by a demo policy id,
for the "I already have a policy" journey. This is deliberately separate from
the PRODUCT catalog (backend/services/policy_service.py): a product describes
what a policy *is*; a record describes what one specific holder actually has
(coverage amount, duration, the benefits and riders THEY hold, limitations).

Lives in ``app/`` (not ``services/``) because only ``app/`` is packaged into
the Lambda, so this is reachable at runtime. The record data ships as
``demo_policy_records.json``; the loader finds it in the repo (backend/data)
or alongside this module when bundled.

Security model (the point of this module)
------------------------------------------
* Every record is FICTIONAL demo data. There is NO real customer data in this
  repo and NO code path from here to a real policy-administration system.
* A policy NUMBER alone must never surface a real person's record. Lookups
  only resolve the strict demo-id form ``DEMO-...``; a bare numeric policy
  number can never match, so it is always treated as unknown.
* Unknown ids never fabricate a policy — ``get_record`` returns ``None``.
* A record states only the riders the holder ACTUALLY has; this module never
  copies the product catalog's optional riders onto a holder.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any

# app/policy_records.py -> backend/
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HERE = os.path.dirname(os.path.abspath(__file__))

# Search order: repo/local layout first, then module-local (bundled) copy.
_CANDIDATE_PATHS = (
    os.path.join(_BACKEND_DIR, "data", "demo_policy_records.json"),
    os.path.join(_HERE, "data", "demo_policy_records.json"),
    os.path.join(_HERE, "demo_policy_records.json"),
)

REQUIRED_DISCLAIMER = (
    "Hackathon Demo — Fictional sample policy record, not a real customer's policy."
)

# A demo record id. The 'DEMO-' prefix is mandatory and is the security guard:
# a real/bare policy number (e.g. "100000123", "LL-93421") can never match
# this pattern, so it can never resolve to a record.
_DEMO_ID_RE = re.compile(r"\bDEMO-[A-Z0-9]+(?:-[A-Z0-9]+)+\b", re.IGNORECASE)

REQUIRED_FIELDS = (
    "id", "product_id", "policy_type", "insurer", "status",
    "coverage_amount", "coverage_duration", "benefits_included",
    "riders_held", "limitations", "document",
)


class PolicyRecordError(RuntimeError):
    """Raised when the demo record store cannot be loaded or breaks its contract."""


def _data_path() -> str | None:
    for path in _CANDIDATE_PATHS:
        if os.path.exists(path):
            return path
    return None


def _validate(record: dict[str, Any]) -> None:
    rid = record.get("id", "<unknown>")
    if not isinstance(rid, str) or not _DEMO_ID_RE.fullmatch(rid):
        raise PolicyRecordError(
            f"record id {rid!r} must be a strict demo id (DEMO-...)")
    if record.get("is_demo_record") is not True:
        raise PolicyRecordError(f"record {rid!r} must set is_demo_record=true")
    if record.get("actual_customer_record") is not False:
        raise PolicyRecordError(
            f"record {rid!r} must set actual_customer_record=false")
    if record.get("demo_disclaimer") != REQUIRED_DISCLAIMER:
        raise PolicyRecordError(f"record {rid!r} must carry the demo disclaimer")
    for field in REQUIRED_FIELDS:
        if field not in record:
            raise PolicyRecordError(
                f"record {rid!r} missing required field {field!r}")
    if not isinstance(record.get("riders_held"), list):
        raise PolicyRecordError(f"record {rid!r} riders_held must be a list")
    document = record.get("document") or {}
    if not document.get("source_ref"):
        raise PolicyRecordError(f"record {rid!r} document needs a source_ref")


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    path = _data_path()
    if not path:
        # Degrade gracefully: no records available rather than crashing a turn.
        return {"records": []}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise PolicyRecordError(f"demo policy records invalid JSON: {exc}") from exc
    records = data.get("records")
    if not isinstance(records, list):
        raise PolicyRecordError("demo policy records has no 'records' list")
    for record in records:
        _validate(record)
    return data


def _normalize_id(raw: str) -> str:
    return (raw or "").strip().upper()


def looks_like_demo_id(text: str) -> bool:
    """True only if the WHOLE string is a valid demo id form. A bare number or
    arbitrary string is not a demo id and will never be looked up."""
    return bool(_DEMO_ID_RE.fullmatch((text or "").strip()))


def find_policy_id_in_text(message: str) -> str | None:
    """Return the first demo policy id mentioned in free text, or None.

    Only matches the strict ``DEMO-...`` form. A message containing only a bare
    policy number yields None by design, so a number alone can never trigger a
    record lookup.
    """
    if not message or not isinstance(message, str):
        return None
    match = _DEMO_ID_RE.search(message)
    return _normalize_id(match.group(0)) if match else None


def get_record(policy_id: str) -> dict[str, Any] | None:
    """Return the demo record for this id, or None for anything that is not an
    exact, known demo id (bare numbers, blanks, unknown ids). Never fabricates."""
    if not policy_id or not looks_like_demo_id(policy_id):
        return None
    wanted = _normalize_id(policy_id)
    for record in _load().get("records", []):
        if _normalize_id(record.get("id", "")) == wanted:
            return record
    return None


def all_record_ids() -> list[str]:
    return [r["id"] for r in _load().get("records", [])]


def _money(record: dict[str, Any]) -> str:
    amount = record.get("coverage_amount")
    currency = record.get("coverage_currency", "USD")
    if isinstance(amount, (int, float)):
        symbol = "$" if currency == "USD" else ""
        return f"{symbol}{amount:,.0f}"
    return "unknown"


def explain_record(record: dict[str, Any]) -> str:
    """Grounded, plain-language explanation of a holder's ACTUAL policy.

    Everything comes from the record itself (coverage amount, duration,
    benefits, the riders the holder actually has, limitations), with the source
    document and page cited. Nothing is pulled from the product catalog, so no
    optional rider is ever assumed onto this holder.
    """
    lines: list[str] = []
    lines.append(f"**{record['demo_disclaimer']}**")
    lines.append("")
    lines.append(f"Here's what your policy record shows (reference {record['id']}):")
    lines.append("")
    lines.append(f"- **Policy type:** {record['policy_type']} "
                 f"({record.get('insurer', 'unknown insurer')})")
    lines.append(f"- **Coverage amount:** {_money(record)} death benefit")
    lines.append(f"- **Duration:** {record['coverage_duration']}")

    benefits = record.get("benefits_included") or []
    if benefits:
        lines.append("- **Included benefits:**")
        for benefit in benefits:
            lines.append(f"  - {benefit}")

    riders = record.get("riders_held") or []
    if riders:
        lines.append("- **Optional riders on your policy:**")
        for rider in riders:
            name = rider.get("name", "rider")
            desc = rider.get("plain_language", "")
            lines.append(f"  - {name}: {desc}".rstrip())
    else:
        lines.append("- **Optional riders on your policy:** none recorded. "
                     "Only riders actually on your policy are shown — product "
                     "brochures list riders that are *available*, not ones you "
                     "necessarily have.")

    limitations = record.get("limitations") or []
    if limitations:
        lines.append("- **Important limitations:**")
        for limitation in limitations:
            lines.append(f"  - {limitation}")

    document = record.get("document") or {}
    source = document.get("source_ref")
    if source:
        cite = source
        page = document.get("page")
        section = document.get("source_section")
        if page:
            cite += f", p. {page}"
        if section:
            cite += f" ({section})"
        lines.append("")
        lines.append(f"_Source: {cite}._")

    lines.append("")
    lines.append("This reflects the fictional sample record on file. If "
                 "anything looks different from your real policy, a licensed "
                 "advisor can confirm the details.")
    return "\n".join(lines)


def unknown_id_message(attempted_id: str | None = None) -> str:
    """A useful, non-inventing reply when an id is not a known demo record."""
    examples = ", ".join(all_record_ids()[:3]) or "DEMO-..."
    attempted = f" \"{attempted_id}\"" if attempted_id else ""
    return (
        f"I couldn't find a policy matching that reference{attempted}, and I "
        "won't guess at a policy's details.\n\n"
        "For this demo, policy lookups use sample reference numbers in the "
        f"form DEMO-XXXX (for example: {examples}). If you have one of those, "
        "share it and I'll walk you through that sample policy. For a real "
        "policy I can explain how coverage generally works, but confirming "
        "your specific details needs a licensed advisor and secure sign-in — a "
        "policy number on its own isn't enough to look up a real record."
    )
