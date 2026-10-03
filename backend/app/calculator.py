"""Deterministic life-insurance needs calculator.

This is the authoritative arithmetic. The LLM must NEVER produce coverage
numbers — it may only explain what this module returns.

Methods (all real, industry-standard):
  * DIME needs analysis  (Debt + Income + Mortgage + Education, net of offsets)
  * Human Life Value (HLV) — present value of future income
  * Income-multiple sanity check (10-15x rule of thumb)
  * Rough term-premium illustration

Design rules from the backend brief:
  * No silently-invented assumptions. Every assumption used is explicit,
    returned in the result, and overridable by the caller.
  * Affordability is NOT used to change the calculated need — it is stored
    as context only (handled in models.py / orchestrator).
  * Results are ILLUSTRATIVE estimates, not quotes or advice. Every result
    carries a disclaimer and the assumptions that produced it.

Pure standard library. No AWS, no network, no third-party packages.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# --------------------------------------------------------------------------
# Default assumptions — in ONE place, all overridable per call. These are
# planning conventions (DIME is used broadly in the industry; 10-15x income
# is a common rule of thumb; HLV is the present-value income approach). They
# are NOT represented as Lincoln Financial's methodology.
# --------------------------------------------------------------------------

DEFAULT_ASSUMPTIONS: dict[str, float] = {
    "income_replacement_years": 10.0,   # DIME "I"
    "education_per_child": 100_000.0,   # common college rule of thumb
    "final_expenses": 15_000.0,         # funeral + final medical, typical US
    "income_multiple_low": 10.0,
    "income_multiple_high": 15.0,
    "hlv_discount_rate": 0.03,          # real discount rate
    "hlv_income_growth": 0.02,          # expected annual income growth
    "hlv_personal_consumption": 0.30,   # share the person spends on self
    "retirement_age": 67.0,
    "max_coverage": 50_000_000.0,       # sanity clamp against typos
}

DISCLAIMER = (
    "This is an illustrative needs assessment based on the information you "
    "provided, not a quote, an offer of insurance, or guaranteed financial "
    "advice. Changing these assumptions will change the estimate. Your actual "
    "coverage and price are set by a licensed insurer after underwriting."
)


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce user input to a float, tolerating strings like '80,000' or '$80000'."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (ValueError, TypeError):
        return default


def _round(value: float, step: int = 1000) -> int:
    return int(round(value / step) * step)


def _clamp_money(value: float, max_coverage: float) -> float:
    return max(0.0, min(round(value), max_coverage))


def resolve_assumptions(overrides: dict[str, Any] | None = None) -> dict[str, float]:
    """Merge caller overrides onto the defaults. Returned so the frontend can
    show and edit exactly what was used — nothing is hidden."""
    merged = dict(DEFAULT_ASSUMPTIONS)
    if overrides:
        for key, val in overrides.items():
            if key in merged and val is not None:
                merged[key] = _num(val, merged[key])
    return merged


@dataclass
class BreakdownLine:
    key: str
    label: str
    detail: str
    amount: int


@dataclass
class NeedsResult:
    """Full, explainable result. `asdict()` gives the frontend everything it
    needs to render the breakdown and the assumptions used."""
    illustrative_gap: int
    gross_need: int
    offsets: int
    breakdown: list[dict]
    offset_lines: list[dict]
    sanity_check: dict
    human_life_value: dict | None
    premium_estimate: dict | None
    assumptions: dict
    flags: list[str]
    explanation: str
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# 1. DIME needs analysis
# --------------------------------------------------------------------------

def calculate_dime(profile: dict, assumptions: dict[str, float]) -> dict:
    annual_income = _num(profile.get("annual_income"))
    years = assumptions["income_replacement_years"]
    non_mortgage_debt = _num(profile.get("non_mortgage_debt"))
    mortgage_balance = _num(profile.get("mortgage_balance"))
    num_children = max(0, int(_num(profile.get("num_children"))))
    per_child = assumptions["education_per_child"]
    final_expenses = assumptions["final_expenses"]

    existing_coverage = _num(profile.get("existing_coverage"))
    liquid_savings = _num(profile.get("liquid_savings"))

    debt_component = non_mortgage_debt + final_expenses
    income_component = annual_income * years
    mortgage_component = mortgage_balance
    education_component = num_children * per_child

    gross_need = debt_component + income_component + mortgage_component + education_component
    offsets = existing_coverage + liquid_savings
    net_need = _clamp_money(gross_need - offsets, assumptions["max_coverage"])

    breakdown = [
        BreakdownLine("debt", "Debt + final expenses",
                      f"Non-mortgage debt ${non_mortgage_debt:,.0f} + final expenses ${final_expenses:,.0f}",
                      _round(debt_component)),
        BreakdownLine("income", "Income replacement",
                      f"${annual_income:,.0f}/yr x {years:g} years",
                      _round(income_component)),
        BreakdownLine("mortgage", "Mortgage payoff",
                      "Remaining mortgage balance",
                      _round(mortgage_component)),
        BreakdownLine("education", "Children's education",
                      (f"{num_children} child(ren) x ${per_child:,.0f}" if num_children
                       else "No children indicated"),
                      _round(education_component)),
    ]
    offset_lines = [
        BreakdownLine("existing_coverage", "Existing life insurance",
                      "Employer + personal policies you already have",
                      _round(existing_coverage)),
        BreakdownLine("liquid_savings", "Savings & liquid assets",
                      "Cash and investments available to your family",
                      _round(liquid_savings)),
    ]

    return {
        "illustrative_gap": _round(net_need),
        "gross_need": _round(gross_need),
        "offsets": _round(offsets),
        "breakdown": [asdict(b) for b in breakdown],
        "offset_lines": [asdict(o) for o in offset_lines],
    }


# --------------------------------------------------------------------------
# 2. Human Life Value (present value of future income)
# --------------------------------------------------------------------------

def calculate_hlv(profile: dict, assumptions: dict[str, float]) -> dict | None:
    age = _num(profile.get("age"))
    if age <= 0:
        return None  # HLV needs an age; skip rather than invent one
    annual_income = _num(profile.get("annual_income"))
    retirement_age = assumptions["retirement_age"]
    growth = assumptions["hlv_income_growth"]
    discount = assumptions["hlv_discount_rate"]
    consumption = assumptions["hlv_personal_consumption"]

    years = max(0, int(round(retirement_age - age)))
    contribution = annual_income * (1 - consumption)

    pv = 0.0
    for t in range(1, years + 1):
        future = contribution * ((1 + growth) ** (t - 1))
        pv += future / ((1 + discount) ** t)

    value = _clamp_money(pv, assumptions["max_coverage"])
    return {
        "value": _round(value),
        "years_to_retirement": years,
    }


# --------------------------------------------------------------------------
# 3. Income-multiple sanity check
# --------------------------------------------------------------------------

def income_multiple_check(profile: dict, assumptions: dict[str, float]) -> dict:
    annual_income = _num(profile.get("annual_income"))
    low = assumptions["income_multiple_low"]
    high = assumptions["income_multiple_high"]
    return {
        "low": _round(annual_income * low),
        "high": _round(annual_income * high),
        "low_multiple": low,
        "high_multiple": high,
    }


# --------------------------------------------------------------------------
# 4. Rough term-premium illustration (NOT a quote)
# --------------------------------------------------------------------------

_PREMIUM_BASE_PER_1000 = 0.9
_AGE_FACTOR = [  # (max_age, multiplier)
    (29, 0.8), (34, 1.0), (39, 1.3), (44, 1.9), (49, 2.9),
    (54, 4.6), (59, 7.5), (64, 12.0), (69, 19.0), (120, 30.0),
]
_HEALTH_FACTOR = {
    "excellent": 0.8, "preferred": 0.8,
    "good": 1.0, "standard": 1.0,
    "average": 1.3,
    "poor": 1.9, "substandard": 1.9,
}
_TERM_FACTOR = {10: 0.75, 15: 0.9, 20: 1.0, 30: 1.4}


def estimate_term_premium(profile: dict, coverage: float) -> dict | None:
    coverage = _num(coverage)
    if coverage <= 0:
        return None
    age = _num(profile.get("age"), 35)
    sex = str(profile.get("sex", "")).lower()
    smoker_raw = profile.get("smoker")
    smoker = smoker_raw is True or bool(
        str(smoker_raw).lower() in ("yes", "true", "smoker", "tobacco")
    )
    health = _HEALTH_FACTOR.get(str(profile.get("health", "good")).lower(), 1.0)
    term_years = int(_num(profile.get("term_years"), 20))

    age_mult = next((m for max_a, m in _AGE_FACTOR if age <= max_a), 30.0)
    sex_mult = 0.85 if sex.startswith("f") else 1.0
    smoker_mult = 2.5 if smoker else 1.0
    term_mult = _TERM_FACTOR.get(term_years, 1.0)

    annual = (coverage / 1000) * _PREMIUM_BASE_PER_1000 * age_mult * sex_mult * smoker_mult * health * term_mult
    monthly = annual / 12
    return {
        "coverage": _round(coverage),
        "term_years": term_years,
        "monthly": max(5, round(monthly)),
        "annual": max(60, round(annual)),
        "is_estimate": True,
    }


# --------------------------------------------------------------------------
# Top-level: run everything and assemble an explainable result
# --------------------------------------------------------------------------

def run_needs_assessment(profile: dict, assumption_overrides: dict | None = None) -> NeedsResult:
    """Primary entry point. `profile` is a plain dict of customer inputs;
    `assumption_overrides` lets the frontend change any assumption and
    recalculate. Returns a fully explainable NeedsResult."""
    assumptions = resolve_assumptions(assumption_overrides)

    dime = calculate_dime(profile, assumptions)
    sanity = income_multiple_check(profile, assumptions)
    hlv = calculate_hlv(profile, assumptions)
    premium = estimate_term_premium(profile, dime["illustrative_gap"])

    annual_income = _num(profile.get("annual_income"))
    flags: list[str] = []
    within_band = (
        annual_income > 0
        and sanity["low"] * 0.6 <= dime["gross_need"] <= sanity["high"] * 1.4
    )
    if annual_income > 0 and not within_band:
        flags.append(
            f"The detailed (DIME) figure of ${dime['gross_need']:,} is outside the quick "
            f"{sanity['low_multiple']:g}-{sanity['high_multiple']:g}x income band "
            f"(${sanity['low']:,}-${sanity['high']:,}). Worth double-checking the inputs."
        )
    if dime["illustrative_gap"] == 0 and dime["gross_need"] > 0:
        flags.append(
            "Existing coverage and savings already meet the estimated need — "
            "additional coverage may not be necessary."
        )

    explanation = (
        f"Based on the information you provided, your family would need about "
        f"${dime['gross_need']:,} to cover debts, replace "
        f"{assumptions['income_replacement_years']:g} years of income, pay off the "
        f"mortgage, and fund education. "
    )
    if dime["offsets"] > 0:
        explanation += (
            f"Subtracting the ${dime['offsets']:,} you already have leaves an "
            f"illustrative gap of about ${dime['illustrative_gap']:,}."
        )
    else:
        explanation += (
            f"With nothing to offset it, the illustrative gap is about "
            f"${dime['illustrative_gap']:,}."
        )

    return NeedsResult(
        illustrative_gap=dime["illustrative_gap"],
        gross_need=dime["gross_need"],
        offsets=dime["offsets"],
        breakdown=dime["breakdown"],
        offset_lines=dime["offset_lines"],
        sanity_check=sanity,
        human_life_value=hlv,
        premium_estimate=premium,
        assumptions=assumptions,
        flags=flags,
        explanation=explanation,
    )
