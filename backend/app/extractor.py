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


def amount_near(text: str, keyword_re: str, window: int = 60) -> float | None:
    """The monetary amount that belongs to a keyword, not just the biggest one.

    Each extractor pulls only the number closest to its own keyword, so a
    sentence like "I make $80,000 a year and owe about $12,000 in credit cards"
    files $80,000 under income and $12,000 under debt, instead of both fields
    grabbing the largest figure in the message.
    """
    best: float | None = None
    best_distance = window + 1
    for match in re.finditer(keyword_re, text, re.IGNORECASE):
        keyword_end = match.end()
        for amount in _MONEY_RE.finditer(text):
            # Prefer an amount that follows the keyword ("mortgage: $180k"),
            # otherwise the closest one on either side ("$180k mortgage").
            distance = (amount.start() - keyword_end
                        if amount.start() >= keyword_end
                        else keyword_end - amount.end())
            if 0 <= distance < best_distance:
                number = _to_number(amount.group(1), amount.group(2))
                if number is None or (amount.group(2) is None and number < _MIN_PLAIN_AMOUNT):
                    continue
                best, best_distance = number, distance
    return best


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
        # "We rent" is an answer to the mortgage question, not silence.
        if re.search(r"\b(rent|renting|rents)\b", text, re.IGNORECASE):
            return 0.0
        return None
    value = amount_near(text, r"\b(mortgage|home loan|house loan)\b")
    if value is not None:
        return value if 0 <= value <= 50_000_000 else None
    if _UNKNOWN_HINT.search(text):
        return None
    # "no mortgage", "I don't have a mortgage" -- an explicit zero.
    if re.search(r"\b(no|zero|none|nothing|don't have|do not have|don't own|do not own|pay off)\b"
                 r".{0,25}\b(mortgage|home loan|house loan)\b", text, re.IGNORECASE) \
       or re.search(r"\b(mortgage|home loan|house loan)\b.{0,25}"
                    r"\b(free and clear|pay off|payed off|paid off|none|nothing|zero)\b",
                    text, re.IGNORECASE):
        return 0.0
    return None


def _extract_non_mortgage_debt(text: str) -> float | None:
    mentions = re.search(
        r"\b(credit card|car loan|student loan|personal loan|loan|loans|debt|debts|"
        r"owe|owing|credit cards)\b", text, re.IGNORECASE)
    if not mentions:
        return None
    # A mortgage number belongs to the mortgage field, not here.
    scrubbed = re.sub(r"\b(mortgage|home loan|house loan)\b", " ", text, flags=re.IGNORECASE)
    value = amount_near(scrubbed, r"\b(credit card|car loan|student loan|personal loan|"
                                r"loan|loans|debt|debts|owe|owing|credit cards)\b")
    if value is not None:
        return value if 0 <= value <= 50_000_000 else None
    if _UNKNOWN_HINT.search(text):
        return None
    if re.search(r"\b(no|zero|none|nothing|don't have|do not have)\b.{0,25}"
                 r"\b(debt|debts|loans?|owe)\b", text, re.IGNORECASE) \
       or re.search(r"\b(debt|debts|loans?)\b.{0,25}\b(no|zero|none|nothing)\b", text, re.IGNORECASE):
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
        value = amount_near(text, r"\b(insurance|coverage|cover|policy|policies|life insurance)\b")
        if value is None:
            value = max(amounts)
        return value if 0 <= value <= 50_000_000 else None
    # Denial is checked BEFORE the employer check: "I don't have life insurance
    # through work" is an explicit zero, not an unknown amount.
    if _NO_COVERAGE_RE.search(text):
        return 0.0
    # "Insurance through work" / "via my employer": cover exists but the amount
    # is not stated, so this stays unknown and gets asked again.
    if re.search(r"\b(through|via|with|from)\s+(?:my\s+|our\s+)?"
                 r"(work|employer|job|company|benefits)\b", text, re.IGNORECASE):
        return None
    if _UNKNOWN_HINT.search(text):
        return None
    return None


def _extract_savings(text: str) -> float | None:
    mentions = re.search(r"\b(savings?|saved|set aside|put aside|investments?|"
                         r"retirement|401k|403b|ira|pension|cash)\b", text, re.IGNORECASE)
    if not mentions:
        return None
    amounts = find_amounts(text)
    if amounts:
        value = amount_near(text, r"\b(savings?|saved|set aside|put aside|investments?|"
                                r"retirement|401k|403b|ira|pension|cash)\b")
        if value is None:
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

# Only route to an assessment when the person is actually talking about life
# insurance / protecting dependants. A bare "I am trying" is far too broad --
# it matched "I am trying to set up my health insurance".
_LIFE_OR_PROTECTION = (r"(life[-\s]?insurance|life[-\s]?cover|life[-\s]?policy|"
                       r"term insurance|permanent insurance|universal life|"
                       r"coverage for (?:my|our|the) (?:family|kids|children|spouse)|"
                       r"protect (?:my|our) (?:family|kids|children|spouse|dependents)|"
                       r"if (?:something|i) (?:happened|die|died|passed|passed away)|"
                       r"if i (?:died|die|passed away)|"
                       r"support my family|replace my income|family couldn'?t afford)")

_ASSESSMENT_INTENT = re.compile(
    r"\b(i (?:need|want|would like|'?d like|am looking|'m looking|am trying|'m trying)"
    r"[^.?!]{0,40}" + _LIFE_OR_PROTECTION + r")"
    r"|\b(help me (?:get|with|find|figure out|work out|calculate)"
    r"[^.?!]{0,30}(?:" + _LIFE_OR_PROTECTION + r"|coverage|cover|how much i need)\b)"
    r"|\b(how much life insurance|how much cover|how much coverage)"
    r"|\b(protect my family|protect our family|what if i died|if i died|"
    r"if something happened to me|would my family be (?:ok|okay|covered))\b"
    r"|\b(needs? analysis|needs? assessment|coverage calculation)\b",
    re.IGNORECASE)

# Educational questions often arrive with a preamble ("Before I answer, what
# exactly is term life insurance?"), so this is no longer anchored to the
# start of the message. "What if I died" is deliberately excluded -- that is a
# needs statement, not an education question.
_EDUCATIONAL_INTENT = re.compile(
    r"\b(what (?!if\b)(?:\w+\s+){0,2}(?:is|are|'s|does|do)\b"
    r"|what (?:kind|type) of\b|about what\b"
    r"|how (?!if\b)(?:\w+\s+){0,2}(?:does|do|would|is)\b"
    r"|why (?:does|do|is|would)\b"
    r"|difference between\b|can you explain\b|could you explain\b"
    r"|tell me about\b|explain\b|walk me through\b)",
    re.IGNORECASE)

# "afford" on its own used to fire on "if my family couldn't afford our
# mortgage", which is a needs statement, not a request for a price. It now
# only counts next to an explicit cost/premium word.
_PRICING_INTENT = re.compile(
    r"\b(how much (?:will|would|does|should|is|would it)|per month|monthly|premiums?|"
    r"price of|cost of|insurance rates?|afford(?:able)? (?:a|the|this|that)? ?"
    r"(?:premium|policy|cover|coverage)|can i afford)\b",
    re.IGNORECASE)

_RECOMMENDATION_INTENT = re.compile(
    r"\b(which|what)\b.{0,40}\b(policy|plan|product|option)\b.{0,25}"
    r"\b(should i|do i|buy|purchase|best|recommend|makes sense|make sense|"
    r"would suit|is right for me|fit my needs)\b|\brecommend\b|\bbest (?:policy|plan|product)\b"
    r"|\b(what|which) (?:kind|type) of (?:insurance|coverage|policy|plan)\b",
    re.IGNORECASE)

_APPROVAL_INTENT = re.compile(
    r"\b(am i (?:approved|qualified)|will (?:you|lincoln) approve|do i qualify|"
    r"underwrit\w*|am i eligible)\b", re.IGNORECASE)


# Topics LifeLine does not cover. These are recognised so the assistant can
# state its scope plainly instead of starting a life-insurance assessment.
_OUT_OF_SCOPE_INTENT = re.compile(
    r"\b(health insurance|medical insurance|dental|vision insurance|"
    r"car insurance|auto insurance|vehicle insurance|home insurance|"
    r"renters insurance|renters'|homeowners|travel insurance|"
    r"disability insurance|long[- ]term care insurance|pet insurance|"
    r"umbrella policy|workers'? comp|life insurance for my (car|house|pet)|"
    r"insure (?:my|our) (?:car|house|home|vehicle|car|pet|health))\b",
    re.IGNORECASE)

# Ordinary chit-chat / unrelated requests that are clearly not about insurance.
_OFF_TOPIC_INTENT = re.compile(
    r"^\s*(what'?s|whats|what is|how'?s|hows|how is)\s+the\s+(weather|forecast|time|news)\b"
    r"|\b(make me|write me|translate|recipe|horoscope|joke)\b",
    re.IGNORECASE)


# A reply that is essentially just a figure, e.g. "About 85k." in answer to
# "roughly what do you earn?". People rarely repeat the question back.
_BARE_ANSWER_MAX_WORDS = 7


def extract_bare_amount(message: str) -> float | None:
    """A single amount said on its own, with no keyword to attach it to.

    The orchestrator decides WHICH field this belongs to (the one it just
    asked about), so this stays safe: it never guesses a field by itself.
    """
    text = (message or "").strip()
    if not text or len(text.split()) > _BARE_ANSWER_MAX_WORDS:
        return None
    amounts = find_amounts(text)
    return amounts[0] if len(amounts) == 1 else None


def classify_intent(message: str) -> str:
    """Route a turn.

    assessment | educational | out_of_scope | pricing | recommendation
    | approval | none
    """
    text = (message or "").strip()
    if not text:
        return "none"
    # Out of scope wins outright: asking about health or car insurance must
    # never fall through to "start an assessment".
    if _OUT_OF_SCOPE_INTENT.search(text):
        return "out_of_scope"
    if _OFF_TOPIC_INTENT.search(text):
        return "out_of_scope"
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