"""Deterministic natural-language -> profile_updates extraction.

Turns what the customer actually said into structured calculator inputs so the
guided assessment can progress without the customer knowing the schema.

DESIGN RULES (deliberate, conservative):
  * Only a value the customer EXPLICITLY stated is ever recorded.
  * Nothing is inferred, guessed, or defaulted.
      - "I have a mortgage"           -> mortgage balance UNKNOWN (ask again)
      - "I have insurance at work"    -> coverage amount UNKNOWN (ask again)
      - "I have two kids"             -> num_children = 2, but no ages invented
  * An explicitly stated zero ("no debts", "nothing saved") IS a real answer
    and is recorded as 0. Absence of a mention is not zero.
  * This module NEVER computes coverage. calculator.py remains the only
    authority for the assessment arithmetic.
  * Pure standard library, fully offline and unit-testable.

Extraction is deliberately regex-based rather than model-based so it is
deterministic, free, and cannot hallucinate a number.
"""
from __future__ import annotations

import re
from typing import Any

# --------------------------------------------------------------------------
# Number parsing
# --------------------------------------------------------------------------

_MULTIPLIERS = {
    "k": 1_000, "thousand": 1_000, "thousands": 1_000,
    "m": 1_000_000, "million": 1_000_000, "millions": 1_000_000,
}

_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
# No leading \b before the suffix: a digit and a letter are both word
# characters, so "85k" has no boundary between them. The trailing \b is what
# stops "3 months" being read as 3 million.
_MONEY_RE = re.compile(
    r"[$£]?\s*" + _NUMBER + r"\s*"
    r"(k|thousand|thousands|m|million|millions)?\b",
    re.IGNORECASE,
)

# An unsuffixed number under 1000 is not a money figure -- "5 kids", "30
# years" are not amounts. Suffixes are trusted at any size ("85k" is 85,000).
_MIN_PLAIN_AMOUNT = 1000.0


def _to_number(value: str, suffix: str | None) -> float | None:
    try:
        amount = float(value.replace(",", ""))
    except (ValueError, AttributeError):
        return None
    if suffix:
        amount *= _MULTIPLIERS.get(suffix.lower(), 1)
    return amount


def find_amounts(text: str) -> list[float]:
    """All monetary-looking numbers in the text, largest last."""
    out: list[float] = []
    for match in _MONEY_RE.finditer(text):
        number = _to_number(match.group(1), match.group(2))
        if number is None:
            continue
        # A bare "5" or "30" is not money unless an explicit suffix said so.
        if match.group(2) is None and number < _MIN_PLAIN_AMOUNT:
            continue
        out.append(number)
    return out


# --------------------------------------------------------------------------
# Word numbers (for child counts)
# --------------------------------------------------------------------------

_WORD_NUMBERS = {
    "zero": 0, "no": 0, "none": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "a": 1, "an": 1, "couple": 2, "few": 3,
}


def find_count(text: str) -> int | None:
    """A small explicit count (children/dependents). Never invented."""
    digit = re.search(r"\b(\d{1,2})\s*(?:kids?|children|dependents?|sons?|daughters?)\b",
                      text, re.IGNORECASE)
    if digit:
        value = int(digit.group(1))
        if 0 <= value <= 20:
            return value
    for word, value in _WORD_NUMBERS.items():
        if re.search(rf"\b{word}\s+(?:kids?|children|dependents?|sons?|daughters?)\b",
                     text, re.IGNORECASE):
            return value
    if re.search(r"\bno\s+(?:kids?|children|dependents?|sons?|daughters?)\b",
                 text, re.IGNORECASE):
        return 0
    return None


# --------------------------------------------------------------------------
# Field-specific extraction. Each returns a value ONLY when stated.
# --------------------------------------------------------------------------

def _extract_income(text: str) -> float | None:
    if not re.search(r"\b(income|earn|earns|earning|make|makes|made|salary|"
                     r"wages?|paycheck|paid|per year|annually|a year)\b",
                     text, re.IGNORECASE):
        return None
    amounts = find_amounts(text)
    if not amounts:
        return None
    value = max(amounts)
    # Plausibility guard: an income outside this range is a parsing artefact,
    # not an income. Leave it out and let the assistant ask again.
    if 1_000 <= value <= 10_000_000:
        return value
    return None


def _extract_age(text: str) -> float | None:
    match = re.search(r"\b(?:i am|i'm|im|aged|age of|age is)\s*(\d{2})\b", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(\d{2})\s*(?:years old|yrs old|yo|y/o)\b", text, re.IGNORECASE)
    if not match:
        return None
    age = int(match.group(1))
    return age if 18 <= age <= 100 else None


def _extract_mortgage(text: str) -> float | None:
    if not re.search(r"\b(mortgage|home loan|house loan)\b", text, re.IGNORECASE):
        return None
    amounts = find_amounts(text)
    if not amounts:
        return None
    value = max(amounts)
    return value if 0 <= value <= 50_000_000 else None


def _extract_non_mortgage_debt(text: str) -> float | None:
    mentions = re.search(
        r"\b(credit card|car loan|student loan|personal loan|loan|loans|debt|debts|"
        r"owe|owing|credit cards)\b", text, re.IGNORECASE)
    if not mentions:
        return None
    # A mortgage number belongs to the mortgage field, not here.
    if re.search(r"\b(mortgage|home loan|house loan)\b", text, re.IGNORECASE):
        amounts = find_amounts(re.sub(r"\b(mortgage|home loan|house loan)\b", " ", text, flags=re.IGNORECASE))
    else:
        amounts = find_amounts(text)
    if amounts:
        value = max(amounts)
        return value if 0 <= value <= 50_000_000 else None
    if _UNKNOWN_HINT.search(text):
        return None
    if re.search(r"\b(no|zero|none|nothing)\b.{0,20}\b(debt|debts|loans?|owe)\b", text, re.IGNORECASE) \
       or re.search(r"\b(debt|debts|loans?)\b.{0,20}\b(no|zero|none|nothing|none left)\b", text, re.IGNORECASE):
        return 0.0
    return None


# A denial of cover/insurance only counts when it negates the noun itself.
# The loose "insurance ... no" direction is unsafe: "I have insurance through
# work but I have no idea how much" would read as zero, which is the opposite
# of the truth.
_NO_COVERAGE_RE = re.compile(
    r"\b(?:no|zero|none|nothing|not any|never)\s+(?:\w+\s+){0,2}"
    r"(?:life\s+|health\s+)?(?:insurance|coverage|cover|policy|policies)\b"
    r"|\b(?:i\s+)?(?:don'?t|do not|dont|doesn'?t|does not)\s+"
    r"(?:have|has|own|carry)\b.{0,25}?\b(?:insurance|coverage|cover|policy|policies)\b"
    r"|\b(?:insurance|coverage|cover|policy|policies)\b\s*(?:is|are|=|:)?\s*"
    r"(?:none|nothing|zero|nil)\b",
    re.IGNORECASE)

# The customer is saying the amount exists but is unknown to them. In that case
# the field is still missing and we must ask again rather than record a zero.
_UNKNOWN_HINT = re.compile(
    r"\b(no idea|don'?t know|do not know|dont know|not sure|unsure|unknown|"
    r"not sure how much|no clue|how much is it|how much do i have|"
    r"how much is my|how much is that)\b",
    re.IGNORECASE)


def _extract_existing_coverage(text: str) -> float | None:
    mentions = re.search(r"\b(insurance|coverage|cover|policy|policies|life insurance)\b",
                         text, re.IGNORECASE)
    if not mentions:
        return None
    amounts = find_amounts(text)
    if amounts:
        value = max(amounts)
        return value if 0 <= value <= 50_000_000 else None
    # "Insurance through work" / "via my employer": cover exists but the amount
    # is not stated, so this stays unknown and gets asked again.
    if re.search(r"\b(through|via|with|from)\s+(?:my\s+|our\s+)?"
                 r"(work|employer|job|company|benefits)\b", text, re.IGNORECASE):
        return None
    if _UNKNOWN_HINT.search(text):
        return None
    if _NO_COVERAGE_RE.search(text):
        return 0.0
    return None


def _extract_savings(text: str) -> float | None:
    mentions = re.search(r"\b(savings?|saved|set aside|put aside|investments?|"
                         r"retirement|401k|403b|ira|pension|cash)\b", text, re.IGNORECASE)
    if not mentions:
        return None
    amounts = find_amounts(text)
    if amounts:
        value = max(amounts)
        return value if 0 <= value <= 100_000_000 else None
    if re.search(r"\b(no|none|nothing|zero)\b.{0,25}\b(sav|set aside|invest|retirement|401k|ira)\w*\b",
                 text, re.IGNORECASE) \
       or re.search(r"\b(sav\w*|set aside|invest\w*|retirement|401k|ira)\b.{0,25}\b(no|none|nothing|zero)\b",
                    text, re.IGNORECASE):
        return 0.0
    return None


def _extract_num_children(text: str) -> Any:
    count = find_count(text)
    return count if count is not None else None


_EXTRACTORS = (
    ("annual_income", _extract_income),
    ("num_children", _extract_num_children),
    ("age", _extract_age),
    ("mortgage_balance", _extract_mortgage),
    ("non_mortgage_debt", _extract_non_mortgage_debt),
    ("existing_coverage", _extract_existing_coverage),
    ("liquid_savings", _extract_savings),
)


def extract_profile_updates(message: str) -> dict[str, Any]:
    """Extract calculator inputs the customer explicitly stated.

    Returns only keys whose values were actually present in the text. A key is
    absent when the customer did not state that value -- absence is never
    turned into 0, and never into a guess.
    """
    if not message or not isinstance(message, str) or not message.strip():
        return {}

    updates: dict[str, Any] = {}
    for key, extractor in _EXTRACTORS:
        try:
            value = extractor(message)
        except Exception:
            value = None  # an extractor bug must never break the turn
        if value is not None:
            updates[key] = value
    return updates


# --------------------------------------------------------------------------
# Intent classification (deterministic, used only to route the turn)
# --------------------------------------------------------------------------

_ASSESSMENT_INTENT = re.compile(
    r"\b(i (?:need|want|am looking|am trying|would like|'m trying|'m looking)\b"
    r"|help me (?:get|with|find)|where do (?:i|we) start|how do i start|"
    r"get (?:a )?quote|get started|set up cover\w*|apply for)",
    re.IGNORECASE)

_EDUCATIONAL_INTENT = re.compile(
    r"^\s*(what is|what are|what's|whats|what does|explain|how does|why does|"
    r"why do|why is|why would|can you explain|tell me about|difference between|"
    r"how is .* different|how much does .* cost|what happens)\b",
    re.IGNORECASE)

_PRICING_INTENT = re.compile(
    r"\b(how much (?:will|would|does|should|is)\b|per month|monthly|premium|"
    r"price of|cost of|rates?|afford)", re.IGNORECASE)

_RECOMMENDATION_INTENT = re.compile(
    r"\b(which|what)\b.{0,40}\b(policy|plan|product|option)\b.{0,25}"
    r"\b(should i|do i|buy|purchase|best|recommend)\b|\brecommend\b|\bbest (?:policy|plan|product)\b",
    re.IGNORECASE)

_APPROVAL_INTENT = re.compile(
    r"\b(am i (?:approved|qualified)|will (?:you|lincoln) approve|do i qualify|"
    r"underwrit\w*|am i eligible)\b", re.IGNORECASE)


def classify_intent(message: str) -> str:
    """Route a turn: assessment | educational | pricing | recommendation | approval."""
    text = (message or "").strip()
    if not text:
        return "none"
    if _PRICING_INTENT.search(text):
        return "pricing"
    if _APPROVAL_INTENT.search(text):
        return "approval"
    if _RECOMMENDATION_INTENT.search(text):
        return "recommendation"
    if _EDUCATIONAL_INTENT.search(text):
        return "educational"
    if _ASSESSMENT_INTENT.search(text):
        return "assessment"
    return "none"