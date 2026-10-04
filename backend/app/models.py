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
#
# `question` is the ONE question the guided assessment asks for this field.
# The orchestrator picks the next field and hands it to the model; the model
# only phrases it. `allows_zero` marks fields where "none"/"nothing" is a
# legitimate answer so we never have to invent a value.
PROFILE_FIELDS: list[dict] = [
    {"key": "annual_income", "label": "Annual income",
     "why": "Drives how much income your family would need to replace.",
     "question": "Roughly what do you earn in a year before tax?",
     "required": True, "allows_zero": False},
    {"key": "num_children", "label": "Children / dependents",
     "why": "Used for education costs and whether coverage is needed at all.",
     "question": "How many children or other dependents rely on your income?",
     "required": True, "allows_zero": True},
    {"key": "mortgage_balance", "label": "Mortgage balance",
     "why": "Often the largest single debt to pay off.",
     "question": "About how much is left on your mortgage?",
     "required": True, "allows_zero": True},
    {"key": "non_mortgage_debt", "label": "Other debts",
     "why": "Credit cards, car and student loans the family would inherit.",
     "question": "Besides the mortgage, roughly how much other debt do you have — "
                 "car loans, credit cards, or student loans?",
     "required": True, "allows_zero": True},
    {"key": "existing_coverage", "label": "Existing life insurance",
     "why": "Employer and personal policies reduce what you still need.",
     "question": "Do you already have any life insurance, and if so roughly how "
                 "much cover?",
     "required": True, "allows_zero": True},
    {"key": "liquid_savings", "label": "Savings & investments",
     "why": "Money already available to your family reduces the gap.",
     "question": "Roughly how much have you set aside for your family?",
     "required": True, "allows_zero": True},
    {"key": "age", "label": "Age",
     "why": "Used only for the advanced calculation and the cost illustration.",
     "question": "Not needed for your estimate.",
     "required": False, "allows_zero": False},
    {"key": "sex", "label": "Sex (for pricing)",
     "why": "Insurers price men and women differently.",
     "question": "Not needed for your estimate.",
     "required": False, "allows_zero": False},
    {"key": "smoker", "label": "Tobacco use",
     "why": "One of the biggest cost factors.",
     "question": "Not needed for your estimate.",
     "required": False, "allows_zero": False},
    {"key": "health", "label": "General health",
     "why": "Affects the price illustration, not the amount needed.",
     "question": "Not needed for your estimate.",
     "required": False, "allows_zero": False},
    {"key": "term_years", "label": "Preferred term length",
     "why": "Used only for the premium illustration.",
     "question": "Not needed for your estimate.",
     "required": False, "allows_zero": False},
]

# The order the guided assessment asks things in. Kept separate from
# PROFILE_FIELDS so the conversational order can change without touching the
# calculation inputs.
#
# These are exactly the six fields the published DIME result depends on. Age
# is deliberately NOT required: it affects only the internal HLV and the
# unpublished premium illustration, never gross_need or illustrative_gap, so
# requiring it would block the primary assessment for no reason. Age support is
# retained in the model and calculator if it is ever needed again.
ASSESSMENT_ORDER: list[str] = [
    "annual_income",
    "num_children",
    "mortgage_balance",
    "non_mortgage_debt",
    "existing_coverage",
    "liquid_savings",
]

# Context fields stored but NOT used to change the calculated need.
#
# The product-preference fields below feed ONLY the inspectable product
# matcher (product_matcher.py). They never change gross_need or the
# illustrative gap -- those come solely from the DIME calculator.
CONTEXT_FIELDS: list[dict] = [
    {"key": "affordability_monthly", "label": "Comfortable monthly budget",
     "why": "Stored as context. It does not change your calculated need; it helps "
            "a human advisor discuss options with you."},
    {"key": "name", "label": "Name", "why": "Used only to personalize your summary."},
    {"key": "coverage_goal", "label": "Primary goal",
     "why": "What the person mainly wants (e.g. temporary protection vs lifetime "
            "cover with cash value). Used only to match product structures."},
    {"key": "coverage_duration_pref", "label": "Preferred coverage length",
     "why": "Temporary (a set number of years) vs lifetime. Used only to match "
            "product structures, never to change the calculated need."},
    {"key": "cash_value_interest", "label": "Interest in cash value",
     "why": "Whether the person wants potential cash-value growth. Used only to "
            "distinguish term from permanent product structures."},
    {"key": "market_exposure_pref", "label": "Cash-value growth style",
     "why": "For permanent cover with cash value: index-linked vs investment "
            "options. Used only to distinguish IUL from VUL product structures, "
            "never to change the calculated need."},
    {"key": "budget_comfort", "label": "Budget preference",
     "why": "Whether lower cost is a priority. Context only; the matcher never "
            "quotes a premium or confirms affordability."},
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

    @property
    def assessment_started(self) -> bool:
        return self.started

    # ---- updating ----
    def update(self, data: dict) -> None:
        """Apply partial updates. Values are routed to profile vs context by
        their key; unknown keys are ignored (never silently stored as
        financial inputs)."""
        for key, value in (data or {}).items():
            if key in _ALL_PROFILE_KEYS:
                self.profile[key] = value
                # A real value supersedes a prior skip: the field is answered.
                if _has_value(value):
                    self.unskip_field(key)
            elif key in _ALL_CONTEXT_KEYS:
                self.context[key] = value
            # unknown keys are intentionally dropped

    def set_assumptions(self, overrides: dict) -> None:
        """Edit the stored assumptions (explicit + editable, per the brief)."""
        self.assumptions = resolve_assumptions({**self._assumption_overrides(), **(overrides or {})})

    # ---- conversation state (persisted in context) ----
    def mark_started(self) -> None:
        """Record that the user has entered an assessment (so we don't start
        collecting financial details off a general/educational question)."""
        self.context["_assessment_started"] = True

    @property
    def started(self) -> bool:
        return bool(self.context.get("_assessment_started"))

    def set_last_asked(self, field_key: str | None) -> None:
        """Remember which field the assistant just asked about, so the next
        message's bare answer can be attributed to it."""
        if field_key:
            self.context["_last_asked_field"] = field_key
        else:
            self.context.pop("_last_asked_field", None)

    @property
    def last_asked_field(self) -> str | None:
        return self.context.get("_last_asked_field")

    # ---- conversation path ----
    # Which journey the customer chose on the homepage:
    #   "coverage" -> new-customer needs assessment (financial intake)
    #   "policy"   -> help understanding an existing policy (NO intake)
    #   "general"  -> just asking questions (NO intake unless they opt in)
    # Stored so the path survives turns, can be switched, and resets on a new
    # chat (a fresh session has no path).
    _VALID_PATHS = ("coverage", "policy", "general")

    def set_path(self, path: str | None) -> None:
        if path in self._VALID_PATHS:
            self.context["_path"] = path
        elif path is None:
            self.context.pop("_path", None)

    @property
    def path(self) -> str | None:
        return self.context.get("_path")

    def skip_field(self, field_key: str) -> None:
        """Mark a required field as skipped: the customer could not or chose
        not to answer. The field stays UNKNOWN (no value is stored, never 0),
        but it is removed from the asking rotation so the assistant does not
        repeat the same question forever."""
        if not field_key:
            return
        skipped = set(self.context.get("_skipped_fields", []))
        skipped.add(field_key)
        self.context["_skipped_fields"] = sorted(skipped)

    def unskip_field(self, field_key: str) -> None:
        """Clear a skip, e.g. the customer later volunteers the value."""
        skipped = set(self.context.get("_skipped_fields", []))
        skipped.discard(field_key)
        if skipped:
            self.context["_skipped_fields"] = sorted(skipped)
        else:
            self.context.pop("_skipped_fields", None)

    @property
    def skipped_fields(self) -> set[str]:
        return set(self.context.get("_skipped_fields", []))

    def note_unclear(self, field_key: str) -> int:
        """Record that a reply about `field_key` could not be understood, and
        return how many consecutive unclear replies we've now had for it. Used
        to escalate from the same question to a clarification with a Skip
        option, instead of repeating identical wording."""
        counts = dict(self.context.get("_unclear_counts", {}))
        counts[field_key] = counts.get(field_key, 0) + 1
        self.context["_unclear_counts"] = counts
        return counts[field_key]

    def clear_unclear(self, field_key: str) -> None:
        counts = dict(self.context.get("_unclear_counts", {}))
        if counts.pop(field_key, None) is not None:
            if counts:
                self.context["_unclear_counts"] = counts
            else:
                self.context.pop("_unclear_counts", None)

    def _assumption_overrides(self) -> dict:
        # current assumptions expressed as overrides (so edits merge, not reset)
        return {k: v for k, v in self.assumptions.items()}

    # ---- status ----
    def missing_fields(self) -> list[str]:
        """Required fields still PENDING a question. Skipped fields are not
        pending (we won't re-ask them) even though they remain unknown."""
        skipped = self.skipped_fields
        return [k for k in _REQUIRED_KEYS
                if not _has_value(self.profile.get(k)) and k not in skipped]

    def unanswered_required(self) -> list[str]:
        """Required fields with no real value, INCLUDING skipped ones. These
        are treated as 0 by the calculator but shown as unknown to the user."""
        return [k for k in _REQUIRED_KEYS if not _has_value(self.profile.get(k))]

    def next_field(self) -> dict | None:
        """The single field the guided assessment should ask about next.

        Determined here, in application code, in a fixed conversational order.
        The language model only phrases the question -- it never chooses which
        field comes next. This is what keeps the assessment deterministic.
        """
        pending = set(self.missing_fields())
        for key in ASSESSMENT_ORDER:
            if key in pending:
                for field in PROFILE_FIELDS:
                    if field["key"] == key:
                        return dict(field)
        return None

    def known_summary(self) -> str:
        """Plain list of facts already captured, so nothing is asked twice."""
        parts: list[str] = []
        for field in PROFILE_FIELDS:
            key = field["key"]
            if key in _REQUIRED_KEYS and _has_value(self.profile.get(key)):
                value = self.profile[key]
                if key in ("annual_income", "mortgage_balance",
                           "non_mortgage_debt", "existing_coverage",
                           "liquid_savings"):
                    try:
                        shown = f"${float(value):,.0f}"
                    except (TypeError, ValueError):
                        shown = str(value)
                elif isinstance(value, float) and value.is_integer():
                    shown = str(int(value))  # 2.0 reads as "2", not "2.0"
                else:
                    shown = str(value)
                parts.append(f"{field['label']}: {shown}")
        return "; ".join(parts)

    def status(self) -> str:
        """collecting -> still missing required inputs.
        ready       -> enough to calculate.
        complete    -> marked complete by the orchestrator after a result is shown."""
        if self.missing_fields():
            return "collecting"
        return "ready"

    def to_dict(self) -> dict:
        next_field = self.next_field()
        return {
            "status": self.status(),
            "missing_fields": self.missing_fields(),
            "next_field": next_field["key"] if next_field else None,
            "next_field_question": next_field["question"] if next_field else None,
            "next_field_why": next_field["why"] if next_field else None,
            "known_summary": self.known_summary(),
            "profile": dict(self.profile),
            "assessment_started": self.started,
            # Hide internal conversation-state keys (prefixed with "_") from the
            # customer-facing contract.
            "context": {k: v for k, v in self.context.items() if not k.startswith("_")},
            "assumptions": dict(self.assumptions),
            "field_help": {f["key"]: f["why"] for f in PROFILE_FIELDS},
        }
