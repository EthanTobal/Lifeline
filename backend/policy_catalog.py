"""LifeLine demo policy catalog.

Loads the fictional hackathon demo policies from ``backend/data/demo_policies.json``
and provides simple lookups by ID, insurance type, and matching tag.

Scope
-----
This module does retrieval only. It deliberately contains **no** matching,
scoring, ranking, or recommendation logic. Which demo policies to surface is a
separate concern that belongs to the needs-assessment layer, per
``knowledge/demo-policies/policy-matching-rules.md``, which requires an
illustrative financial need to be calculated *before* any product comparison.

Every policy in this catalog is a fictional hackathon product. Nothing here is an
actual Lincoln Financial product, quote, premium, guarantee, or recommendation,
and this catalog must never be merged into the Lincoln RAG corpus or uploaded to
the Bedrock Knowledge Base.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Iterable

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "demo_policies.json")

# Label that must be rendered on every demo policy card.
DEMO_UI_LABEL = "Hackathon Demo - Not an actual Lincoln Financial product or quote"


class PolicyCatalogError(RuntimeError):
    """Raised when the catalog cannot be loaded or is malformed."""


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    """Load and cache the demo policy catalog."""
    if not os.path.exists(DATA_PATH):
        raise PolicyCatalogError(f"demo policy catalog not found at {DATA_PATH}")
    try:
        with open(DATA_PATH, encoding="utf-8") as handle:
            catalog = json.load(handle)
    except json.JSONDecodeError as exc:
        raise PolicyCatalogError(f"demo policy catalog is not valid JSON: {exc}") from exc

    policies = catalog.get("policies")
    if not isinstance(policies, list):
        raise PolicyCatalogError("demo policy catalog has no 'policies' list")

    for policy in policies:
        # These flags are the safety contract; a wrong value is a bug worth failing on.
        if policy.get("is_demo_product") is not True:
            raise PolicyCatalogError(
                f"policy {policy.get('id')!r} must set is_demo_product=true")
        if policy.get("actual_lincoln_product") is not False:
            raise PolicyCatalogError(
                f"policy {policy.get('id')!r} must set actual_lincoln_product=false")
    return catalog


def list_policies() -> list[dict[str, Any]]:
    """Return every demo policy, in catalog order."""
    return list(load_catalog().get("policies", []))


def get_policy(policy_id: str) -> dict[str, Any] | None:
    """Return one demo policy by ID, or None if no such policy exists."""
    for policy in list_policies():
        if policy.get("id") == policy_id:
            return policy
    return None


def get_policies_by_type(insurance_type: str) -> list[dict[str, Any]]:
    """Return demo policies whose insurance type matches, case-insensitively.

    Matching is substring-based so callers can pass a family such as
    ``"term"`` or ``"universal"`` without knowing the exact label wording.
    """
    needle = (insurance_type or "").strip().lower()
    if not needle:
        return []
    return [p for p in list_policies()
            if needle in (p.get("insurance_type") or "").lower()]


def get_policies_by_tags(tags: Iterable[str]) -> list[dict[str, Any]]:
    """Return demo policies carrying **all** of the supplied tags.

    Comparison is case-insensitive. An empty tag list returns an empty list
    rather than every policy, so a missing signal cannot silently look like a
    broad match.
    """
    wanted = {t.strip().lower() for t in (tags or []) if t and t.strip()}
    if not wanted:
        return []
    return [p for p in list_policies()
            if wanted.issubset({t.lower() for t in p.get("matching_tags", [])})]


def get_all_tags() -> list[str]:
    """Return every distinct matching tag across the catalog, sorted."""
    tags: set[str] = set()
    for policy in list_policies():
        tags.update(policy.get("matching_tags", []))
    return sorted(tags)


def is_demo_product(policy: dict[str, Any]) -> bool:
    """True when a policy dict is flagged as a fictional demo product."""
    return policy.get("is_demo_product") is True


if __name__ == "__main__":  # quick manual inspection
    for policy in list_policies():
        print(f"{policy['id']:<30} {policy['insurance_type']}")
    print(f"\ntags: {', '.join(get_all_tags())}")