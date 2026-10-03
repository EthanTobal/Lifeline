"""LifeLine demo policy service.

Loads the fictional hackathon demo policies from
``backend/data/demo_policies.json`` and exposes simple, reusable lookups.

Scope
-----
This module does retrieval only. It deliberately does NOT:

* calculate life-insurance needs or determine a coverage amount
* make financial recommendations, rank policies, or score "best" options
* call Gemini or perform any Bedrock retrieval
* modify the customer profile or manage customer sessions
* store conversation history

Matching here is structural (filtering on declared fields), not advisory. Callers
should describe results as demo structures that are "relevant" or that "match
stated preferences", never as a recommended, best, or must-buy policy.

Every policy in this catalog is a fictional hackathon product. Nothing here is an
actual Lincoln Financial product, quote, offer, premium, guarantee, eligibility
rule, or recommendation, and this catalog must never be merged into the Lincoln
RAG corpus or uploaded to the Bedrock Knowledge Base.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Iterable

# backend/services/policy_service.py -> backend/
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BACKEND_DIR, "data", "demo_policies.json")

# Required disclaimer text on every demo policy.
REQUIRED_DISCLAIMER = (
    "Hackathon Demo — Not an actual Lincoln Financial product or quote."
)

S3_BUCKET = "lifeline-project-data-714047902595"
S3_KEY_PREFIX = "policies/"


class PolicyServiceError(RuntimeError):
    """Raised when the policy catalog cannot be loaded or violates its contract."""


def _validate(policy: dict[str, Any]) -> None:
    """Enforce the demo-product safety contract.

    These flags are the guardrail that stops fictional demo policies from ever
    being presented as real Lincoln Financial products, so a wrong value is a
    bug worth failing loudly on rather than silently tolerating.
    """
    policy_id = policy.get("id", "<unknown>")
    if policy.get("is_demo_product") is not True:
        raise PolicyServiceError(
            f"policy {policy_id!r} must set is_demo_product=true")
    if policy.get("actual_lincoln_product") is not False:
        raise PolicyServiceError(
            f"policy {policy_id!r} must set actual_lincoln_product=false")
    if policy.get("demo_disclaimer") != REQUIRED_DISCLAIMER:
        raise PolicyServiceError(
            f"policy {policy_id!r} must carry the required demo disclaimer")


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    """Load, validate, and cache the demo policy catalog."""
    if not os.path.exists(DATA_PATH):
        raise PolicyServiceError(f"demo policy catalog not found at {DATA_PATH}")
    try:
        with open(DATA_PATH, encoding="utf-8") as handle:
            catalog = json.load(handle)
    except json.JSONDecodeError as exc:
        raise PolicyServiceError(
            f"demo policy catalog is not valid JSON: {exc}") from exc

    policies = catalog.get("policies")
    if not isinstance(policies, list):
        raise PolicyServiceError("demo policy catalog has no 'policies' list")
    for policy in policies:
        _validate(policy)
    return catalog


def get_all_policies() -> list[dict[str, Any]]:
    """Return every demo policy, in catalog order."""
    return list(load_catalog().get("policies", []))


def get_policy(policy_id: str) -> dict[str, Any] | None:
    """Return the demo policy with this ID, or None if no such policy exists."""
    if not policy_id:
        return None
    for policy in get_all_policies():
        if policy.get("id") == policy_id:
            return policy
    return None


def filter_policies(insurance_type: str | None = None,
                    matching_tags: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Return demo policies matching the supplied structural filters.

    Both arguments are optional and combine with AND.

    * ``insurance_type`` matches the policy's ``insurance_type`` exactly
      (case-insensitively), e.g. ``"term"``.
    * ``matching_tags`` returns policies carrying **all** the given tags,
      e.g. ``["permanent_coverage", "cash_value"]``.

    An empty or omitted filter matches everything, which makes this a plain
    retrieval helper. It expresses no opinion about which policies suit a
    customer; deciding what to surface is the needs-assessment layer's job.
    """
    policies = get_all_policies()

    if insurance_type:
        wanted_type = insurance_type.strip().lower()
        policies = [p for p in policies
                    if (p.get("insurance_type") or "").lower() == wanted_type]

    if matching_tags is not None:
        wanted_tags = {t.strip().lower() for t in matching_tags if t and t.strip()}
        policies = [p for p in policies
                    if wanted_tags.issubset(
                        {t.lower() for t in p.get("matching_tags", [])})]

    return policies


def get_policy_document(policy_id: str) -> dict[str, Any] | None:
    """Return the document metadata (including S3 bucket/key) for a policy."""
    policy = get_policy(policy_id)
    if policy is None:
        return None
    return policy.get("document")


def get_document_location(policy_id: str) -> dict[str, str] | None:
    """Return just the S3 coordinates needed to later mint a presigned URL.

    Returns ``{"s3_bucket": ..., "s3_key": ...}`` or None when the policy is
    unknown or has no document. Intended as the hand-off point for a future
    backend component that generates a temporary presigned URL; this module
    deliberately does no AWS work and holds no credentials.
    """
    document = get_policy_document(policy_id)
    if not document:
        return None
    bucket, key = document.get("s3_bucket"), document.get("s3_key")
    if not bucket or not key:
        return None
    return {"s3_bucket": bucket, "s3_key": key}


def get_all_tags() -> list[str]:
    """Return every distinct matching tag across the catalog, sorted."""
    tags: set[str] = set()
    for policy in get_all_policies():
        tags.update(policy.get("matching_tags", []))
    return sorted(tags)


if __name__ == "__main__":  # quick manual inspection
    for policy in get_all_policies():
        print(f"{policy['id']:<30} {policy['insurance_type']}")
    print(f"\ntags: {', '.join(get_all_tags())}")