"""Customer profile + assessment data model.

Tracks what the customer has told us, what is still missing, and the
assessment status (collecting -> ready -> complete). Assumptions used by
the calculator are stored here so they are explicit, returned to the
frontend, and editable.

Affordability is stored as CONTEXT only. Per the backend brief, it must
NOT automatically change the calculated financial need.

Pure standard library — this is a plain data layer with no AWS or AI.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .calculator import DEFAULT_ASSUMPTIONS, resolve_assumptions


# Fields that drive the calculation. `required` fields gate readiness.
# Each carries a plain-language "why" to help the frontend explain itself
# (important for an older, non-technical audience).
PROFILE_FIELDS: list[dict] = [
    {"key": "annual_income", "label": "Annual income",
     "why": "Drives how much income your family would need to replace.", "required": True},
    {"key": "num_children", "label": "Children / dependents",
     "why": "Used for education costs and whether coverage is needed at all.", "required": False},
    {"key": "mortgage_balance", "label": "Mortgage balance",
     "why": "Often the largest single debt to pay off.", "required": False},
    {"key": "non_mortgage_debt", "label": "Other debts",
     "why": "Credit cards, car and student loans the family would inherit.", "required": False},
    {"key": "existing_coverage", "label": "Existing life insurance",
     "why": "Employer and personal policies reduce what you still need.", "required": False},
    {"key": "liquid_savings", "label": "Savings & investments",
     "why": "Money already available to your family reduces the gap.", "required": False},
    {"key": "age", "label": "Age",
     "why": "Used for the advanced calculation and the cost illustration.", "required": False},
    {"key": "sex", "label": "Sex (for pricing)",
     "why": "Insurers price men and women differently.", "required": False},
    {"key": "smoker", "label": "Tobacco use",
     "why": "One of the biggest cost factors.", "required": False},
    {"key": "health", "label": "General health",
     "why": "Affects the price illustration, not the amount needed.", "required": False},
    {"key": "term_years", "label": "Preferred term length",
     "why": "Used only for the premium illustration.", "required": False},
]

# Context fields stored but NOT used to change the calculated need.
CONTEXT_FIELDS: list[dict] = [
    {"key": "affordability_monthly", "label": "Comfortable monthly budget",
     "why": "Stored as context. It does not change your calculated need; it helps "
            "a human advisor discuss options with you."},
    {"key": "name", "label": "Name", "why": "Used only to personalize your summary."},
]

_REQUIRED_KEYS = [f["key"] for f in PROFILE_FIELDS if f["required"]]
_ALL_PROFILE_KEYS = [f["key"] for f in PROFILE_FIELDS]
_ALL_CONTEXT_KEYS = [f["key"] for f in CONTEXT_FIELDS]


def _has_value(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, bool):
        return True
    return bool(v)


@dataclass
class Assessment:
    """Everything we know about one customer session's assessment."""
    profile: dict = field(default_factory=dict)        # calculation inputs
    context: dict = field(default_factory=dict)        # affordability, name, etc.
    assumptions: dict = field(default_factory=lambda: dict(DEFAULT_ASSUMPTIONS))

    # ---- updating ----
    def update(self, data: dict) -> None:
        """Apply partial updates. Values are routed to profile vs context by
        their key; unknown keys are ignored (never silently stored as
        financial inputs)."""
        for key, value in (data or {}).items():
            if key in _ALL_PROFILE_KEYS:
                self.profile[key] = value
            elif key in _ALL_CONTEXT_KEYS:
                self.context[key] = value
            # unknown keys are intentionally dropped

    def set_assumptions(self, overrides: dict) -> None:
        """Edit the stored assumptions (explicit + editable, per the brief)."""
        self.assumptions = resolve_assumptions({**self._assumption_overrides(), **(overrides or {})})

    def _assumption_overrides(self) -> dict:
        # current assumptions expressed as overrides (so edits merge, not reset)
        return {k: v for k, v in self.assumptions.items()}

    # ---- status ----
    def missing_fields(self) -> list[str]:
        return [k for k in _REQUIRED_KEYS if not _has_value(self.profile.get(k))]

    def status(self) -> str:
        """collecting -> still missing required inputs.
        ready       -> enough to calculate.
        complete    -> marked complete by the orchestrator after a result is shown."""
        if self.missing_fields():
            return "collecting"
        return "ready"

    def to_dict(self) -> dict:
        return {
            "status": self.status(),
            "missing_fields": self.missing_fields(),
            "profile": dict(self.profile),
            "context": dict(self.context),
            "assumptions": dict(self.assumptions),
            "field_help": {f["key"]: f["why"] for f in PROFILE_FIELDS},
        }
