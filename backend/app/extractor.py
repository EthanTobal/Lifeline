"""Natural-language -> profile_updates extraction.

Turns what the customer actually said into structured calculator inputs so the
guided assessment can progress without the customer knowing the schema.

DESIGN RULES (deliberate, conservative):
  * Only a value the customer EXPLICITLY stated is ever recorded.
  * Each money amount binds to the field whose keyword is NEAREST to it, so a
    single sentence with several numbers maps each number to the right field.
    We never take "the biggest number in the message" for a field.
      - "I make $90k and owe $180k on the house, $100k cover through work"
        -> income 90,000 ; mortgage 180,000 ; coverage 100,000 (not all 180k)
  * A bare answer with no field keyword ("10k", "10k for family") attaches to
    the field the assistant just asked about (`asked_field`).
  * FUTURE GOALS are not current balances. "I want to save $10k" / "my goal is
    to have $10k" is NOT recorded as current savings.
  * Nothing is inferred, guessed, or defaulted.
      - "I have a mortgage"           -> mortgage balance UNKNOWN (ask again)
      - "I have insurance at work"    -> coverage amount UNKNOWN (ask again)
      - "I have two kids"             -> num_children = 2, but no ages invented
  * An explicitly stated zero ("no debts", "nothing saved") IS a real answer
    and is recorded as 0. Absence of a mention is not zero.
  * This module NEVER computes coverage. calculator.py remains the only
    authority for the assessment arithmetic.
  * Pure standard library, fully offline and unit-testable.

Extraction is deliberately rule-based rather than model-based so it is
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


class _Amount:
    """A money amount and where it sits in the text, so we can bind it to the
    nearest field keyword rather than guessing with max()."""
    __slots__ = ("value", "start", "end")

    def __init__(self, value: float, start: int, end: int) -> None:
        self.value = value
        self.start = start
        self.end = end


def _scan_amounts(text: str) -> list[_Amount]:
    """Every monetary-looking number in the text, with positions."""
    out: list[_Amount] = []
    for match in _MONEY_RE.finditer(text):
        number = _to_number(match.group(1), match.group(2))
        if number is None:
            continue
        # A bare "5" or "30" is not money unless an explicit suffix said so.
        if match.group(2) is None and number < _MIN_PLAIN_AMOUNT:
            continue
        out.append(_Amount(number, match.start(), match.end()))
    out.extend(_scan_word_amounts(text))
    return out


def find_amounts(text: str) -> list[float]:
    """Backwards-compatible: just the amount values (smallest-to-largest order
    is not guaranteed; callers that need positions use _scan_amounts)."""
    return [a.value for a in _scan_amounts(text)]


# --------------------------------------------------------------------------
# Spelled-out money amounts ("ninety thousand dollars", "a hundred and
# twenty thousand"). Voice transcription in particular tends to produce
# words rather than digits, and without this the extractor silently returns
# no update for the turn -- which, combined with a static fallback prompt,
# makes the assistant ask the exact same question again verbatim.
# --------------------------------------------------------------------------

_WORD_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_WORD_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_WORD_SCALES = {"thousand": 1_000, "million": 1_000_000, "grand": 1_000}

_WORD_NUM_TOKEN = (
    r"(?:a|an|and|zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|"
    r"hundred|thousand|million|grand)"
)
_WORD_AMOUNT_RE = re.compile(
    rf"\b{_WORD_NUM_TOKEN}(?:[\s-]+{_WORD_NUM_TOKEN})*\b", re.IGNORECASE
)


def _words_to_number(phrase: str) -> float | None:
    """Parse a spelled-out number phrase. Returns None unless it reads as a
    real amount (mirrors the digit path: needs a scale word, or to resolve to
    at least `_MIN_PLAIN_AMOUNT`), so bare small words like "two" or "a" in
    unrelated sentences are never mistaken for money."""
    tokens = phrase.lower().replace("-", " ").split()
    total = 0.0
    current = 0.0
    saw_quantifier = False
    saw_large_scale = False
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("a", "an"):
            # Only a numeral when it introduces a scale ("a hundred", "a
            # thousand"/"a million"/"a grand"). A trailing "a" that belongs to
            # the rest of the sentence ("...a year") is not part of the
            # amount, so stop here rather than miscounting it as +1.
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt == "hundred" or nxt in _WORD_SCALES:
                current += 1
                saw_quantifier = True
            else:
                break
        elif tok == "and":
            pass
        elif tok in _WORD_ONES:
            current += _WORD_ONES[tok]
            saw_quantifier = True
        elif tok in _WORD_TENS:
            current += _WORD_TENS[tok]
            saw_quantifier = True
        elif tok == "hundred":
            if not saw_quantifier:
                return None
            current = (current or 1) * 100
        elif tok in _WORD_SCALES:
            if not saw_quantifier:
                # A bare "thousand"/"million" with no quantifier before it is
                # not a standalone amount -- it's almost always the tail of a
                # digit+word amount ("90 thousand") already handled elsewhere.
                return None
            if saw_large_scale:
                # A second large scale ("ninety thousand ... fifty thousand")
                # means two separate amounts, not one sum. Stop at the first;
                # the scanner emits each number group as its own _Amount.
                return None
            total += (current or 1) * _WORD_SCALES[tok]
            current = 0
            saw_large_scale = True
        i += 1
    total += current
    if not saw_quantifier:
        return None
    if not saw_large_scale and total < _MIN_PLAIN_AMOUNT:
        return None
    return total


# Tokens that may appear inside a spelled-out amount but are not themselves
# the number ("and", "a", "an"). They must never widen an amount's recorded
# span, or the amount drifts toward a neighbouring field's keyword and binds
# to the wrong field (the "ninety thousand and owe fifty thousand" bug).
_CONNECTIVE_WORDS = {"and", "a", "an"}
_WORD_TOKEN_RE = re.compile(r"[a-z]+", re.IGNORECASE)


def _scan_word_amounts(text: str) -> list[_Amount]:
    """Emit one _Amount per spelled-out number group.

    A single regex run like "ninety thousand and fifty thousand" is SPLIT at
    each large-scale boundary into separate amounts, and each amount's span is
    tightened to the number words themselves (leading/trailing "and"/"a"/"an"
    are excluded) so nearest-keyword binding stays accurate.
    """
    out: list[_Amount] = []
    for match in _WORD_AMOUNT_RE.finditer(text):
        # Walk the individual word tokens inside this run, accumulating a group
        # and closing it at each large scale word (thousand/million/grand).
        group: list[tuple[str, int, int]] = []  # (word, start, end)
        for m in _WORD_TOKEN_RE.finditer(match.group(0)):
            word = m.group(0).lower()
            abs_start = match.start() + m.start()
            abs_end = match.start() + m.end()
            group.append((word, abs_start, abs_end))
            if word in _WORD_SCALES:
                _emit_group(group, out)
                group = []
        _emit_group(group, out)
    return out


def _emit_group(group: list[tuple[str, int, int]], out: list[_Amount]) -> None:
    if not group:
        return
    phrase = " ".join(w for w, _, _ in group)
    value = _words_to_number(phrase)
    if value is None:
        return
    # Tighten the span to exclude leading/trailing connective words so the
    # amount sits exactly over its own number, not a neighbouring keyword.
    numeric = [(w, s, e) for (w, s, e) in group if w not in _CONNECTIVE_WORDS]
    if not numeric:
        return
    start = numeric[0][1]
    end = numeric[-1][2]
    out.append(_Amount(value, start, end))


# --------------------------------------------------------------------------
# Field keyword definitions. Each field lists the keywords that signal it.
# An amount is attributed to the field whose keyword is closest to it.
# --------------------------------------------------------------------------

_FIELD_KEYWORDS: dict[str, list[str]] = {
    "annual_income": [
        r"income", r"earn\w*", r"make\b", r"makes\b", r"made\b", r"salary",
        r"wages?", r"paycheck", r"paid\b", r"per year", r"a year", r"annually",
    ],
    "mortgage_balance": [r"mortgage", r"home loan", r"house loan", r"on the house",
                         r"on the home", r"house\b", r"home\b"],
    "non_mortgage_debt": [r"credit cards?", r"car loan", r"student loan",
                          r"personal loan", r"loans?", r"debts?", r"owe", r"owing"],
    "existing_coverage": [r"insurance", r"coverage", r"covered", r"cover\b",
                          r"policy", r"policies", r"through work", r"at work",
                          r"employer"],
    "liquid_savings": [r"savings?", r"saved", r"set aside", r"put aside",
                       r"investments?", r"invested", r"retirement", r"401k",
                       r"403b", r"ira", r"pension", r"nest egg", r"in the bank"],
}

# Compile a single finder per field that yields keyword match positions.
_FIELD_KEYWORD_RE: dict[str, re.Pattern] = {
    field: re.compile("|".join(f"(?:{kw})" for kw in kws), re.IGNORECASE)
    for field, kws in _FIELD_KEYWORDS.items()
}

# Plausibility clamps per field (reject parsing artefacts, never guess).
_FIELD_CLAMP = {
    "annual_income": (1_000, 10_000_000),
    "mortgage_balance": (0, 50_000_000),
    "non_mortgage_debt": (0, 50_000_000),
    "existing_coverage": (0, 50_000_000),
    "liquid_savings": (0, 100_000_000),
}

# Mortgage keywords win over the generic debt keywords when both are near the
# same number (a mortgage figure must not also become "other debt").
_DEBT_YIELDS_TO_MORTGAGE = True

# Future-goal / hypothetical wording: when present, a money amount for savings
# (or coverage) describes an intention, not a current balance, so we do not
# record it. "I want to save 10k", "planning to put aside 10k", "hope to have".
_FUTURE_GOAL_RE = re.compile(
    r"\b(want to|wanna|hope to|hoping to|plan(?:ning)? to|planning on|"
    r"aiming to|aim to|goal is|my goal|would like to|'?d like to|like to save|"
    r"trying to|intend to|thinking about|thinking of|save up|build up to|"
    r"get to)\b",
    re.IGNORECASE)


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
# Core: bind each amount to its nearest field keyword
# --------------------------------------------------------------------------

def _nearest_field_for(amount: _Amount, keyword_hits: dict[str, list[int]]) -> str | None:
    """Return the field whose keyword is closest to this amount, or None if no
    field keyword is in the message at all."""
    best_field = None
    best_dist = None
    for field, positions in keyword_hits.items():
        for pos in positions:
            # distance from the amount span to the keyword position
            if pos < amount.start:
                dist = amount.start - pos
            elif pos > amount.end:
                dist = pos - amount.end
            else:
                dist = 0
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_field = field
    return best_field


def _collect_keyword_hits(text: str) -> dict[str, list[int]]:
    hits: dict[str, list[int]] = {}
    for field, pattern in _FIELD_KEYWORD_RE.items():
        positions = [m.start() for m in pattern.finditer(text)]
        if positions:
            hits[field] = positions
    return hits


def _resolve_amount_field(field: str, text: str, amount: _Amount) -> str:
    """Mortgage figures must not also be read as 'other debt'. If an amount was
    attributed to non_mortgage_debt but a mortgage keyword sits right next to
    it, hand it to mortgage instead."""
    if field == "non_mortgage_debt" and _DEBT_YIELDS_TO_MORTGAGE:
        window = text[max(0, amount.start - 25): amount.end + 25]
        if re.search(r"\b(mortgage|home loan|house loan|on the house|on the home)\b",
                     window, re.IGNORECASE):
            return "mortgage_balance"
    return field


# --------------------------------------------------------------------------
# Zero / unknown handling (explicit "none", "I don't know")
# --------------------------------------------------------------------------

_UNKNOWN_HINT = re.compile(
    r"\b(no idea|don'?t know|do not know|dont know|not sure|unsure|unknown|"
    r"not sure how much|no clue|how much is it|how much do i have|"
    r"how much is my|how much is that)\b",
    re.IGNORECASE)

_NONE_SAVINGS = re.compile(
    r"\b(no|none|nothing|zero)\b.{0,25}\b(sav|set aside|put aside|invest|retirement|401k|ira|nest egg)\w*\b"
    r"|\b(sav\w*|set aside|put aside|invest\w*|retirement|401k|ira|nest egg)\b.{0,25}\b(no|none|nothing|zero)\b"
    r"|\bnothing (?:saved|set aside|put aside)\b",
    re.IGNORECASE)

_NONE_DEBT = re.compile(
    r"\b(no|zero|none|nothing)\b.{0,20}\b(debt|debts|loans?|owe)\b"
    r"|\b(debt|debts|loans?)\b.{0,20}\b(no|zero|none|nothing|none left)\b"
    r"|\bdebt[- ]?free\b|\bpaid off\b",
    re.IGNORECASE)

_NO_COVERAGE_RE = re.compile(
    r"\b(?:no|zero|none|nothing|not any|never)\s+(?:\w+\s+){0,2}"
    r"(?:life\s+|health\s+)?(?:insurance|coverage|cover|policy|policies)\b"
    r"|\b(?:i\s+)?(?:don'?t|do not|dont|doesn'?t|does not)\s+"
    r"(?:have|has|own|carry)\b.{0,25}?\b(?:insurance|coverage|cover|policy|policies)\b"
    r"|\b(?:insurance|coverage|cover|policy|policies)\b\s*(?:is|are|=|:)?\s*"
    r"(?:none|nothing|zero|nil)\b",
    re.IGNORECASE)

# Coverage that EXISTS but amount is unknown ("through work") -> stay unknown.
_COVERAGE_UNKNOWN_AMOUNT = re.compile(
    r"\b(through|via|with|from)\s+(?:my\s+|our\s+)?"
    r"(work|employer|job|company|benefits)\b", re.IGNORECASE)


def _clamp(field: str, value: float) -> float | None:
    lo, hi = _FIELD_CLAMP.get(field, (0, 10**12))
    return value if lo <= value <= hi else None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def extract_profile_updates(message: str, asked_field: str | None = None) -> dict[str, Any]:
    """Extract calculator inputs the customer explicitly stated.

    `asked_field` is the field the assistant just asked about. A bare answer
    with no field keyword of its own (e.g. "10k", "10k for family") is bound to
    that field — so short, natural answers work without a magic phrase.

    Returns only keys whose values were actually present. Absence is never
    turned into 0, and never into a guess.
    """
    if not message or not isinstance(message, str) or not message.strip():
        return {}
    text = message.strip()
    updates: dict[str, Any] = {}

    # 1. Children count (independent of money).
    count = find_count(text)
    if count is not None:
        updates["num_children"] = count

    # 2. Age (independent of money).
    age = _extract_age(text)
    if age is not None:
        updates["age"] = age

    # 3. Money amounts, each bound to its nearest field keyword.
    amounts = _scan_amounts(text)
    keyword_hits = _collect_keyword_hits(text)
    is_future_goal = bool(_FUTURE_GOAL_RE.search(text))

    # Track the best (nearest) amount chosen per field, so repeated keywords
    # don't double-count; each field gets exactly one value from this message.
    chosen: dict[str, float] = {}
    for amount in amounts:
        field = _nearest_field_for(amount, keyword_hits)
        if field is None:
            # No field keyword anywhere near this number. If the assistant just
            # asked about a specific field, attribute a bare answer to it.
            if asked_field in _FIELD_CLAMP:
                field = asked_field
            else:
                continue
        field = _resolve_amount_field(field, text, amount)
        # Future goals are not current balances (applies to savings/coverage).
        if is_future_goal and field in ("liquid_savings", "existing_coverage"):
            continue
        clamped = _clamp(field, amount.value)
        if clamped is None:
            continue
        # Keep the amount nearest its keyword; first assignment wins when a
        # field already has a value from a closer keyword earlier in the loop.
        if field not in chosen:
            chosen[field] = clamped
    updates.update(chosen)

    # 4. Explicit zeros / "none" when no amount was captured for that field.
    if "liquid_savings" not in updates and not is_future_goal and _NONE_SAVINGS.search(text):
        updates["liquid_savings"] = 0.0
    if "non_mortgage_debt" not in updates and _NONE_DEBT.search(text):
        updates["non_mortgage_debt"] = 0.0
    if "existing_coverage" not in updates:
        # "through work" with no number -> leave unknown (ask again).
        if _COVERAGE_UNKNOWN_AMOUNT.search(text) or _UNKNOWN_HINT.search(text):
            pass
        elif _NO_COVERAGE_RE.search(text):
            updates["existing_coverage"] = 0.0

    # 5. Bare "none"/"nothing"/"zero" answered DIRECTLY to the field we just
    #    asked about. "What other debts do you have?" -> "none" means 0 for
    #    that field, even though the word carries no field keyword of its own.
    #    Only for zero-allowing money fields, only when the user did not also
    #    state an unknown/skip (handled separately) or a future goal.
    if (asked_field in _ZERO_ALLOWED_FIELDS
            and asked_field not in updates
            and not is_future_goal
            and not _UNKNOWN_HINT.search(text)
            and _BARE_NONE_RE.fullmatch(text.strip().rstrip(".!"))):
        updates[asked_field] = 0.0

    return updates


# Required money fields where an explicit zero is a legitimate answer. Income
# is deliberately excluded: "I earn nothing" is not a normal assessment input.
_ZERO_ALLOWED_FIELDS = {
    "mortgage_balance", "non_mortgage_debt", "existing_coverage", "liquid_savings",
}

# A short, standalone "no / none / nothing / zero / nope" reply.
_BARE_NONE_RE = re.compile(
    r"(?:no|none|nope|nothing|zero|nil|n/?a|not any|i have none|i have nothing)",
    re.IGNORECASE)

# An EXPLICIT instruction to skip: the user is choosing to move on. We honor
# this immediately (field stays UNKNOWN, never zero).
_EXPLICIT_SKIP_RE = re.compile(
    r"\b(skip(?:\s+(?:it|this|that|the question))?|pass|move on|"
    r"next question|rather not say|prefer not to say|prefer not)\b",
    re.IGNORECASE)

# Softer UNCERTAINTY: "I don't know", "no idea", "not sure". On its own this is
# not an instruction to skip -- the user may just need the question reworded or
# a rough estimate. We clarify first and only skip if it persists.
_UNCERTAIN_RE = re.compile(
    r"\b(i don'?t know|dont know|do not know|not sure|unsure|no idea|no clue|"
    r"i'?m not sure|can'?t remember|cannot remember|don'?t remember|"
    r"not now|maybe later)\b",
    re.IGNORECASE)


def wants_to_skip(message: str) -> bool:
    """True when the reply EXPLICITLY asks to skip the question (not merely
    expresses uncertainty). Used by the orchestrator to advance past a field,
    leaving it UNKNOWN (never zero)."""
    text = (message or "").strip()
    if not text:
        return False
    return bool(_EXPLICIT_SKIP_RE.search(text))


def is_uncertain(message: str) -> bool:
    """True when the reply expresses not-knowing without explicitly skipping.
    The orchestrator uses this to offer a clarification + skip option rather
    than repeating the identical question."""
    text = (message or "").strip()
    if not text:
        return False
    return bool(_UNCERTAIN_RE.search(text))


def _extract_age(text: str) -> float | None:
    match = re.search(r"\b(?:i am|i'm|im|aged|age of|age is)\s*(\d{2})\b", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\b(\d{2})\s*(?:years old|yrs old|yo|y/o)\b", text, re.IGNORECASE)
    if not match:
        return None
    age = int(match.group(1))
    return age if 18 <= age <= 100 else None


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