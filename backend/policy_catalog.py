"""Backwards-compatible shim for the LifeLine demo policy catalog.

The canonical implementation now lives in ``backend/services/policy_service.py``.
This module is retained so earlier imports keep working, and simply delegates.

New code should import the service directly::

    from backend.services import policy_service

    policy_service.get_policy("lifeline-term-20")
    policy_service.filter_policies(insurance_type="term")

Every policy is a fictional hackathon demo product. Nothing here is an actual
Lincoln Financial product, quote, offer, premium, guarantee, or recommendation.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Iterable

# Make ``backend.services`` importable regardless of the caller's working directory.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from services.policy_service import (  # noqa: E402
    PolicyServiceError,
    REQUIRED_DISCLAIMER,
    get_all_policies,
    get_all_tags,
    get_document_location,
    get_policy,
    get_policy_document,
    load_catalog,
)

# Retained for backwards compatibility with the previous public name.
PolicyCatalogError = PolicyServiceError

# Retained for backwards compatibility with the previous public name.
DEMO_UI_LABEL = REQUIRED_DISCLAIMER


def list_policies() -> list[dict[str, Any]]:
    """Deprecated alias for :func:`get_all_policies`."""
    return get_all_policies()


def get_policies_by_type(insurance_type: str) -> list[dict[str, Any]]:
    """Deprecated alias for ``filter_policies(insurance_type=...)``.

    Note this previously matched on a substring; the service now matches the
    ``insurance_type`` field exactly.
    """
    return get_all_policies() if not insurance_type else [
        p for p in get_all_policies()
        if insurance_type.strip().lower() == (p.get("insurance_type") or "").lower()
    ]


def get_policies_by_tags(tags: Iterable[str]) -> list[dict[str, Any]]:
    """Deprecated alias for ``filter_policies(matching_tags=...)``."""
    from services import policy_service

    return policy_service.filter_policies(matching_tags=tags)


def is_demo_product(policy: dict[str, Any]) -> bool:
    """True when a policy dict is flagged as a fictional demo product."""
    return policy.get("is_demo_product") is True


__all__ = [
    "DEMO_UI_LABEL",
    "PolicyCatalogError",
    "PolicyServiceError",
    "REQUIRED_DISCLAIMER",
    "get_all_policies",
    "get_all_tags",
    "get_document_location",
    "get_policies_by_tags",
    "get_policies_by_type",
    "get_policy",
    "get_policy_document",
    "is_demo_product",
    "list_policies",
    "load_catalog",
]