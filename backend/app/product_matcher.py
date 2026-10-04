"""Deterministic, inspectable product matching for the new-customer path.

Given a validated profile, the existing coverage calculator's result (the
illustrative gap), and a few product preferences, this module suggests ONE
demo product from the structured catalog and explains why, in terms tied to
the user's stated needs.

Design constraints (deliberate):
  * EXPLICIT, INSPECTABLE RULES. Matching is a fixed, ordered list of rules in
    `MATCH_RULES`; each rule carries the human-readable reason it fires. There
    is no model call and no hidden scoring — you can read exactly why a product
    was chosen.
  * Product ids come ONLY from the catalog (policy_service). Never invent one.
  * Coverage reuses the calculator's `illustrative_gap` verbatim. This module
    NEVER computes a coverage amount of its own.
  * No premiums, rates, or affordability claims. Budget is a context signal
    only; "comfortable budget" is never turned into "you can afford this".
  * Ask only the MISSING question needed to distinguish the remaining options,
    one at a time, reusing anything already provided.
  * If evidence is thin, return a PROVISIONAL suggestion and/or a clarification
    rather than forcing a confident answer.
  * If no additional coverage is indicated (gap <= 0), do NOT push a product.

Pure standard library + the catalog service. No AWS, no AI, fully testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# Catalog access. The catalog lives in backend/services/policy_service.py,
# which is NOT inside app/. To keep the matcher usable both in the repo and in
# the app-only Lambda package, resolve the catalog defensively: prefer the real
# service, and fall back to loading the same JSON directly if the service
# module is not importable.
# ---------------------------------------------------------------------------

import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_HERE)
_CATALOG_PATHS = (
    os.path.join(_BACKEND_DIR, "data", "demo_policies.json"),
    os.path.join(_HERE, "data", "demo_policies.json"),
    os.path.join(_HERE, "demo_policies.json"),
)


def _load_catalog_policies() -> list[dict[str, Any]]:
    # 1. Prefer the authoritative service if it is importable.
    try:
        import sys
        if _BACKEND_DIR not in sys.path:
            sys.path.insert(0, _BACKEND_DIR)
        from services.policy_service import get_all_policies as _svc_all  # type: ignore
        return list(_svc_all())
    except Exception:
        pass
    # 2. Fall back to reading the catalog JSON directly (same data).
    for path in _CATALOG_PATHS:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    return list(json.load(handle).get("policies", []))
            except (OSError, json.JSONDecodeError):
                return []
    return []


def _catalog_ids() -> set[str]:
    return {p.get("id") for p in _load_catalog_policies() if p.get("id")}


def _policy(policy_id: str) -> dict[str, Any] | None:
    for p in _load_catalog_policies():
        if p.get("id") == policy_id:
            return p
    return None


# ---------------------------------------------------------------------------
# Preference signals (read from the assessment; never change the calculation)
# ---------------------------------------------------------------------------

# Normalised preference values the rules reason over.
DURATION_TEMPORARY = "temporary"      # a set number of years
DURATION_LIFETIME = "lifetime"        # permanent / lifelong
CASH_VALUE_YES = "yes"
CASH_VALUE_NO = "no"
MARKET_INDEX = "index"                # index-linked growth (IUL)
MARKET_INVESTMENT = "investment"      # investment-option exposure (VUL)
MARKET_NONE = "none"


@dataclass
class Signals:
    """The distilled preference signals the rules use. Any field may be None
    when the user has not told us yet; the matcher then asks for the single
    most useful missing one."""
    duration: str | None = None          # temporary | lifetime
    cash_value: str | None = None         # yes | no
    market_exposure: str | None = None    # index | investment | none
    term_length_years: int | None = None  # e.g. 20, 30 (if stated)
    budget_priority: bool | None = None   # True if low cost is a stated priority


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("yes", "true", "y", "1"):
            return True
        if v in ("no", "false", "n", "0"):
            return False
    return None


def derive_signals(profile: dict | None, context: dict | None) -> Signals:
    """Translate stored profile/context into normalised matching signals.

    Everything here is read-only interpretation of what the user already said.
    It never writes back and never affects the needs calculation.
    """
    profile = profile or {}
    context = context or {}
    s = Signals()

    # Duration preference: explicit field, or inferred from a stated term length.
    dur = (context.get("coverage_duration_pref") or "").strip().lower()
    if dur in ("temporary", "term", "set period", "fixed"):
        s.duration = DURATION_TEMPORARY
    elif dur in ("lifetime", "permanent", "whole life", "forever", "life"):
        s.duration = DURATION_LIFETIME

    term_years = profile.get("term_years")
    if isinstance(term_years, (int, float)) and term_years > 0:
        s.term_length_years = int(term_years)
        if s.duration is None:
            s.duration = DURATION_TEMPORARY  # a stated term implies temporary

    # Cash-value interest.
    cv = _as_bool(context.get("cash_value_interest"))
    if cv is True:
        s.cash_value = CASH_VALUE_YES
    elif cv is False:
        s.cash_value = CASH_VALUE_NO

    # Market exposure preference.
    me = (context.get("market_exposure_pref") or "").strip().lower()
    if me in ("index", "indexed", "index-linked"):
        s.market_exposure = MARKET_INDEX
    elif me in ("investment", "investments", "market", "variable"):
        s.market_exposure = MARKET_INVESTMENT
    elif me in ("none", "no"):
        s.market_exposure = MARKET_NONE

    # Goal text can imply duration / cash-value when not set explicitly.
    goal = (context.get("coverage_goal") or "").strip().lower()
    if goal:
        if s.duration is None:
            if any(w in goal for w in ("mortgage", "income replace", "while the kids",
                                       "until", "temporary", "term", "children grow")):
                s.duration = DURATION_TEMPORARY
            elif any(w in goal for w in ("lifetime", "whole life", "forever",
                                         "final expenses", "legacy", "estate",
                                         "cash value", "build cash")):
                s.duration = DURATION_LIFETIME
        if s.cash_value is None and any(
                w in goal for w in ("cash value", "build cash", "savings", "invest")):
            s.cash_value = CASH_VALUE_YES

    # Budget priority (context only; never a premium or affordability claim).
    bp = _as_bool(context.get("budget_comfort"))
    if bp is None:
        bc = (context.get("budget_comfort") or "").strip().lower()
        if bc in ("low", "lower", "cheapest", "affordable", "tight", "lowest cost"):
            bp = True
    s.budget_priority = bp
    return s


# ---------------------------------------------------------------------------
# The inspectable rules. Each rule is (name, predicate, product_id, reason).
# Evaluated top to bottom; the first whose predicate is satisfied wins. Every
# product_id below is validated against the catalog at match time.
# ---------------------------------------------------------------------------

def _r_term20(s: Signals) -> bool:
    return (s.duration == DURATION_TEMPORARY
            and (s.cash_value in (None, CASH_VALUE_NO))
            and (s.term_length_years is None or s.term_length_years <= 20))


def _r_term30(s: Signals) -> bool:
    return (s.duration == DURATION_TEMPORARY
            and (s.cash_value in (None, CASH_VALUE_NO))
            and s.term_length_years is not None and s.term_length_years > 20)


def _r_iul(s: Signals) -> bool:
    return (s.duration == DURATION_LIFETIME
            and s.cash_value == CASH_VALUE_YES
            and s.market_exposure in (MARKET_INDEX, None, MARKET_NONE))


def _r_vul(s: Signals) -> bool:
    return (s.duration == DURATION_LIFETIME
            and s.cash_value == CASH_VALUE_YES
            and s.market_exposure == MARKET_INVESTMENT)


# name, predicate, product_id, reason-template
MATCH_RULES: list[tuple[str, Any, str, str]] = [
    ("term20_short_temporary", _r_term20, "lifeline-term-20",
     "You described temporary protection for a set period of around 20 years or "
     "less, and term cover matches a time-bound need at a generally lower relative cost."),
    ("term30_long_temporary", _r_term30, "lifeline-term-30",
     "You described temporary protection but over a longer horizon (more than 20 "
     "years), which a 30-year term keeps in place for longer while staying term cover."),
    ("iul_lifetime_index", _r_iul, "lifeline-indexed-protection",
     "You described wanting lifetime cover with potential cash value, and an "
     "indexed universal life structure illustrates index-linked growth potential "
     "(growth is not guaranteed)."),
    ("vul_lifetime_investment", _r_vul, "lifeline-variable-protection",
     "You described wanting lifetime cover with cash value tied to investment "
     "options, which a variable universal life structure illustrates (with greater "
     "market exposure and risk)."),
]


@dataclass
class MatchResult:
    """Structured, inspectable match output."""
    suggested_product_id: str | None
    estimated_additional_coverage: int | None   # from the calculator gap, verbatim
    provisional: bool
    reasons: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    rule_fired: str | None = None
    no_additional_coverage: bool = False
    signals: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# One-at-a-time clarifying questions. Each distinguishing signal has exactly
# one question. We ask for the single most useful missing signal, in a fixed
# order, reusing anything already provided.
# ---------------------------------------------------------------------------

QUESTIONS: dict[str, dict[str, str]] = {
    "duration": {
        "key": "coverage_duration_pref",
        "question": "Would you like coverage for a set number of years "
                    "(temporary), or cover that can last your whole life (permanent)?",
    },
    "term_length_years": {
        "key": "term_years",
        "question": "Roughly how many years would you like the coverage to last "
                    "— for example 20 years or 30 years?",
    },
    "cash_value": {
        "key": "cash_value_interest",
        "question": "Alongside the payout for your family, are you interested in "
                    "a policy that can build cash value over time?",
    },
    "market_exposure": {
        "key": "market_exposure_pref",
        "question": "For that cash value, would you prefer growth linked to a "
                    "market index, or tied to investment options you choose?",
    },
}


def next_matching_question(signals: Signals) -> dict | None:
    """Return the ONE question that best narrows the remaining options, or None
    if we already have enough to produce a confident match. Reuses anything
    already known: a signal that is set is never asked again."""
    # 1. Duration is the top-level split (term vs permanent).
    if signals.duration is None:
        return QUESTIONS["duration"]

    if signals.duration == DURATION_TEMPORARY:
        # Only need the term length to choose between the two term products.
        if signals.term_length_years is None:
            return QUESTIONS["term_length_years"]
        return None

    if signals.duration == DURATION_LIFETIME:
        # Permanent: confirm cash-value interest, then market exposure to split
        # IUL vs VUL.
        if signals.cash_value is None:
            return QUESTIONS["cash_value"]
        if signals.cash_value == CASH_VALUE_YES and signals.market_exposure is None:
            return QUESTIONS["market_exposure"]
        return None
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def match_product(profile: dict | None,
                  calculator_result: dict | None,
                  context: dict | None = None) -> MatchResult:
    """Suggest a catalog product for a new customer.

    Parameters
    ----------
    profile : the validated assessment profile (calculation inputs).
    calculator_result : the needs-assessment result dict; only
        ``illustrative_gap`` is read, and it is reused verbatim as the estimated
        additional coverage. This function never computes coverage itself.
    context : the assessment context holding preference signals.
    """
    context = context or {}
    gap = None
    if isinstance(calculator_result, dict):
        gap = calculator_result.get("illustrative_gap")

    signals = derive_signals(profile, context)
    sig_dict = {k: v for k, v in asdict(signals).items() if v is not None}

    # Guardrail 1: if the calculator indicates no additional coverage is
    # needed, do NOT push a product.
    if isinstance(gap, (int, float)) and gap <= 0:
        return MatchResult(
            suggested_product_id=None,
            estimated_additional_coverage=int(gap),
            provisional=False,
            no_additional_coverage=True,
            reasons=["Your assessment shows your existing coverage and savings "
                     "already meet the estimated need, so no additional cover is "
                     "indicated right now."],
            assumptions=["Based on the illustrative needs assessment and the "
                         "information you provided."],
            signals=sig_dict,
        )

    # Ask for the single most useful missing distinguisher before committing.
    pending = next_matching_question(signals)

    # Try the explicit rules in order.
    fired_name = None
    product_id = None
    reason = None
    valid_ids = _catalog_ids()
    for name, predicate, pid, rule_reason in MATCH_RULES:
        if predicate(signals):
            # Only ever suggest a product that exists in the catalog.
            if pid in valid_ids:
                fired_name, product_id, reason = name, pid, rule_reason
            break

    # Build limitations/assumptions from the chosen product's catalog entry.
    limitations: list[str] = []
    assumptions: list[str] = []
    if product_id:
        policy = _policy(product_id)
        if policy:
            lim = policy.get("limitations")
            if isinstance(lim, list):
                limitations = list(lim)
            # Catalog riders are AVAILABLE, not held: surface as an assumption,
            # never as a confirmed feature of a specific plan.
            if policy.get("optional_riders") not in (None, "unknown", []):
                assumptions.append("Optional riders shown in the product catalog "
                                   "are available add-ons, not confirmed for any "
                                   "specific plan.")

    assumptions.append("Suggestion is based on the information you provided and "
                       "the illustrative needs assessment; it is not a quote, a "
                       "price, or confirmation that a policy is affordable.")
    if signals.budget_priority:
        assumptions.append("You indicated lower cost is a priority. This matcher "
                           "does not quote premiums; a licensed advisor can confirm "
                           "actual pricing and affordability.")

    unresolved: list[str] = []
    if pending:
        unresolved.append(pending["question"])

    # Decide provisional vs confident.
    #  - No rule fired yet (not enough signal) -> provisional, with a question.
    #  - A rule fired but a distinguishing question is still pending -> still a
    #    real suggestion, but mark provisional and keep the question.
    if product_id is None:
        # Insufficient evidence: provisional, lead with a clarification. Offer a
        # sensible provisional lean only when duration is at least known.
        provisional_reason = (
            "I don't yet have enough detail to match a specific product. "
            "Answering the question below will let me suggest one.")
        return MatchResult(
            suggested_product_id=None,
            estimated_additional_coverage=(int(gap) if isinstance(gap, (int, float)) else None),
            provisional=True,
            reasons=[provisional_reason],
            limitations=[],
            assumptions=assumptions,
            unresolved_questions=unresolved or [QUESTIONS["duration"]["question"]],
            rule_fired=None,
            signals=sig_dict,
        )

    reasons = [reason]
    # Tie the reason to the actual computed need when we have it.
    if isinstance(gap, (int, float)):
        reasons.append(
            f"Your illustrative assessment shows about ${int(gap):,} of additional "
            "coverage; this product structure is one way to cover a need of that size.")

    return MatchResult(
        suggested_product_id=product_id,
        estimated_additional_coverage=(int(gap) if isinstance(gap, (int, float)) else None),
        provisional=bool(pending),
        reasons=reasons,
        limitations=limitations,
        assumptions=assumptions,
        unresolved_questions=unresolved,
        rule_fired=fired_name,
        signals=sig_dict,
    )


# ---------------------------------------------------------------------------
# Follow-up comparisons.
#
# When the customer asks "what are my other options?" or "why not product X?",
# we compare catalog products against their CURRENT signals, using the same
# inspectable rules. For each product we explain, strictly from the catalog:
#   * what it does (plain language),
#   * its documented benefits and the most important tradeoff,
#   * why it fits the stated needs LESS closely than the best match, and
#   * what change in the customer's priorities would make it a better fit.
#
# Everything is sourced from the catalog entry. Facts the catalog marks
# "unknown" (eligibility, availability, real pricing, riders) are surfaced as
# explicitly-unknown rather than invented. Products outside the catalog are
# never fabricated; a resolver reports when a named product is not in it.
# ---------------------------------------------------------------------------

# Each catalog product's "ideal" signal profile, keyed by insurance_type. This
# is how we say *why* a product fits less closely and *what would change that*,
# in terms of the same signals the matcher reasons over. It mirrors MATCH_RULES
# (term <=20 / term >20 / IUL / VUL) without duplicating the decision logic.
_IDEAL_BY_TYPE: dict[str, dict[str, Any]] = {
    "term": {
        "duration": DURATION_TEMPORARY,
        "cash_value": CASH_VALUE_NO,
        "market_exposure": None,
        "summary": "a set-period need with no cash-value growth",
    },
    "indexed_universal_life": {
        "duration": DURATION_LIFETIME,
        "cash_value": CASH_VALUE_YES,
        "market_exposure": MARKET_INDEX,
        "summary": "lifelong cover with index-linked cash-value growth",
    },
    "variable_universal_life": {
        "duration": DURATION_LIFETIME,
        "cash_value": CASH_VALUE_YES,
        "market_exposure": MARKET_INVESTMENT,
        "summary": "lifelong cover with investment-based cash-value growth",
    },
}

# Human phrases for each signal value, used to build "would fit better if".
_SIGNAL_PHRASE = {
    ("duration", DURATION_TEMPORARY): "you wanted coverage for a set number of years",
    ("duration", DURATION_LIFETIME): "you wanted coverage that can last your whole life",
    ("cash_value", CASH_VALUE_YES): "you wanted the policy to build cash value",
    ("cash_value", CASH_VALUE_NO): "you did not need the policy to build cash value",
    ("market_exposure", MARKET_INDEX): "you preferred index-linked cash-value growth",
    ("market_exposure", MARKET_INVESTMENT): "you preferred investment-based cash-value growth",
    ("market_exposure", MARKET_NONE): "you did not want market-linked growth",
}


def _unknown_catalog_fields(policy: dict) -> list[str]:
    """Fields the catalog explicitly marks "unknown" for this product, so a
    comparison can acknowledge what is undocumented rather than inventing it."""
    out: list[str] = []
    for key, value in policy.items():
        if value == "unknown":
            out.append(key)
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if sub_value == "unknown":
                    out.append(f"{key}.{sub_key}")
    return sorted(out)


def _distance_from_ideal(signals: Signals, ideal: dict) -> int:
    """How many of the signals the customer HAS stated diverge from a product's
    ideal profile. Lower = fits the stated needs more closely. Unstated signals
    don't count against a product (we don't penalise what we haven't asked)."""
    d = 0
    if signals.duration is not None and ideal.get("duration") is not None \
            and signals.duration != ideal["duration"]:
        d += 1
    if signals.cash_value is not None and ideal.get("cash_value") is not None \
            and signals.cash_value != ideal["cash_value"]:
        d += 1
    if signals.market_exposure is not None and ideal.get("market_exposure") is not None \
            and signals.market_exposure != ideal["market_exposure"]:
        d += 1
    return d


def _mismatch_reasons(signals: Signals, ideal: dict) -> tuple[list[str], list[str]]:
    """Return (why_it_fits_less_closely, would_fit_better_if) for one product,
    based only on signals the customer has actually stated."""
    less: list[str] = []
    better: list[str] = []
    checks = [
        ("duration", signals.duration),
        ("cash_value", signals.cash_value),
        ("market_exposure", signals.market_exposure),
    ]
    for key, have in checks:
        want = ideal.get(key)
        if have is None or want is None or have == want:
            continue
        have_phrase = _SIGNAL_PHRASE.get((key, have))
        want_phrase = _SIGNAL_PHRASE.get((key, want))
        if key == "duration":
            less.append("you described wanting "
                        + ("temporary" if have == DURATION_TEMPORARY else "lifelong")
                        + " cover, which isn't what this structure is built for")
        elif key == "cash_value":
            if have == CASH_VALUE_NO:
                less.append("you weren't looking for cash-value growth, which is a "
                            "main feature of this structure")
            else:
                less.append("you were interested in building cash value, which this "
                            "structure doesn't provide")
        elif key == "market_exposure":
            less.append("your preferred cash-value growth style differs from this "
                        "product's")
        if want_phrase:
            better.append("it would fit better if " + want_phrase)
    return less, better


def _benefits(policy: dict, limit: int = 3) -> list[str]:
    b = policy.get("benefits")
    return list(b)[:limit] if isinstance(b, list) else []


def _primary_tradeoff(policy: dict) -> str | None:
    lim = policy.get("limitations")
    if isinstance(lim, list) and lim:
        return lim[0]
    return policy.get("tradeoffs")


def _compare_entry(policy: dict, signals: Signals, best_id: str | None) -> dict:
    """Build one product's structured comparison entry, entirely from catalog
    data plus the customer's stated signals."""
    pid = policy.get("id")
    itype = policy.get("insurance_type") or ""
    ideal = _IDEAL_BY_TYPE.get(itype, {})
    less, better = _mismatch_reasons(signals, ideal)
    is_best = (pid == best_id)

    # When this product diverges in no stated signal but still isn't the top
    # pick, give an honest, non-fabricated note rather than an empty reason.
    if not less and not is_best:
        less = ["it's a reasonable fit too; the suggested option just lines up "
                "slightly more closely with what you've told me so far"]

    return {
        "product_id": pid,
        "name": policy.get("display_name") or policy.get("policy_type") or pid,
        "policy_type": policy.get("policy_type"),
        "what_it_does": policy.get("plain_language") or policy.get("description"),
        "benefits": _benefits(policy),
        "primary_tradeoff": _primary_tradeoff(policy),
        "coverage_duration": policy.get("coverage_period"),
        "fits_less_closely_because": less,
        "would_fit_better_if": better,
        # Explicitly acknowledge what the catalog does NOT document, so a
        # comparison never implies these are known.
        "undocumented": _unknown_catalog_fields(policy),
        "is_current_suggestion": is_best,
        "distance": _distance_from_ideal(signals, ideal),
    }


# How many alternatives to surface up front so the customer isn't overwhelmed.
# The rest remain available on request (support_all=True returns everything).
_INITIAL_ALTERNATIVES = 2


def compare_products(profile: dict | None,
                     calculator_result: dict | None,
                     context: dict | None = None,
                     *,
                     focus_product_id: str | None = None,
                     include_all: bool = False) -> dict:
    """Structured, catalog-grounded comparison for follow-up questions.

    Parameters
    ----------
    focus_product_id : when the customer asks specifically about one product
        ("why not whole life?"), that product is always included and flagged,
        even if it wouldn't make the initial short list.
    include_all : when True, return every catalog product (for "show me all my
        options"); otherwise return only the most relevant alternatives plus the
        current suggestion, to avoid overwhelming the customer.

    The decision of which product fits BEST is delegated to match_product (same
    inspectable rules). This function never computes coverage and never quotes a
    price.
    """
    context = context or {}
    best = match_product(profile, calculator_result, context).to_dict()
    best_id = best.get("suggested_product_id")
    signals = derive_signals(profile, context)

    policies = _load_catalog_policies()
    entries = [_compare_entry(p, signals, best_id) for p in policies if p.get("id")]

    # Order by closeness to the stated needs (closest first); the current
    # suggestion always leads.
    entries.sort(key=lambda e: (not e["is_current_suggestion"], e["distance"]))

    # Alternatives are everything except the current suggestion.
    alternatives = [e for e in entries if not e["is_current_suggestion"]]

    shown = alternatives
    if not include_all:
        shown = alternatives[:_INITIAL_ALTERNATIVES]

    # If the customer named a specific product, make sure it's present and
    # flagged, pulling it in even when it's past the initial cut.
    focus_entry = None
    if focus_product_id:
        focus_entry = next((e for e in entries if e["product_id"] == focus_product_id), None)
        if focus_entry and focus_entry not in shown and not focus_entry["is_current_suggestion"]:
            shown = [focus_entry] + [e for e in shown if e["product_id"] != focus_product_id]

    current = next((e for e in entries if e["is_current_suggestion"]), None)

    return {
        "current_suggestion": current,            # may be None (no match yet / no need)
        "current_suggestion_id": best_id,
        "no_additional_coverage": bool(best.get("no_additional_coverage")),
        "alternatives": shown,                    # the ones to show now
        "alternatives_total": len(alternatives),  # how many exist in total
        "has_more": (not include_all) and len(alternatives) > len(shown),
        "focus_product_id": focus_product_id,
        "focus_in_catalog": (focus_entry is not None) if focus_product_id else None,
        "signals": {k: v for k, v in asdict(signals).items() if v is not None},
    }


# ---------------------------------------------------------------------------
# Resolve a product the customer names in free text ("why not whole life?").
# Catalog-only: anything not in the catalog returns None so the caller can
# honestly say it isn't one of the demo options rather than inventing facts.
# ---------------------------------------------------------------------------

# Natural phrases -> insurance_type. "Whole life" is not a distinct catalog
# product; it maps to the permanent structures the catalog does model, and the
# caller should say so rather than pretend a whole-life entry exists.
_TYPE_ALIASES: dict[str, str] = {
    "term": "term",
    "term life": "term",
    "indexed": "indexed_universal_life",
    "index": "indexed_universal_life",
    "iul": "indexed_universal_life",
    "indexed universal": "indexed_universal_life",
    "variable": "variable_universal_life",
    "vul": "variable_universal_life",
    "variable universal": "variable_universal_life",
}


def resolve_product_query(text: str) -> dict:
    """Map free text to a catalog product id when possible.

    Returns {"product_id", "insurance_type", "in_catalog", "named_term",
    "note"}. `in_catalog` is False when the customer named something real but
    not modelled here (e.g. "whole life"), so the caller can acknowledge the
    gap without fabricating a product.
    """
    t = (text or "").lower()
    result = {"product_id": None, "insurance_type": None, "in_catalog": False,
              "named_term": None, "note": None}

    policies = _load_catalog_policies()

    # 1. Direct id or display-name hit.
    for p in policies:
        pid = (p.get("id") or "").lower()
        name = (p.get("display_name") or "").lower()
        if pid and pid in t:
            result.update(product_id=p["id"], insurance_type=p.get("insurance_type"),
                          in_catalog=True, named_term=p.get("display_name"))
            return result
        if name and name in t:
            result.update(product_id=p["id"], insurance_type=p.get("insurance_type"),
                          in_catalog=True, named_term=p.get("display_name"))
            return result

    # 2. "Whole life" / "permanent" — real concepts the catalog models only as
    #    IUL/VUL. Acknowledge rather than invent a whole-life product.
    if "whole life" in t or "permanent" in t:
        result.update(named_term="whole life" if "whole life" in t else "permanent",
                      insurance_type=None, in_catalog=False,
                      note="The demo catalog doesn't include a standalone whole-life "
                           "product. The permanent options it does model are indexed "
                           "universal life and variable universal life.")
        return result

    # 3. Type aliases (term / indexed / variable). Pick the first catalog
    #    product of that type (term defaults to the shorter term example).
    for alias, itype in _TYPE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", t):
            match = next((p for p in policies
                          if (p.get("insurance_type") or "") == itype), None)
            if match:
                result.update(product_id=match["id"], insurance_type=itype,
                              in_catalog=True, named_term=alias)
            else:
                result.update(named_term=alias, insurance_type=itype, in_catalog=False)
            return result

    return result
