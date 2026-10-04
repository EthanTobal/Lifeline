"""Orchestration layer.

Ties together: customer profile/session -> deterministic calculator ->
Bedrock KB retrieval -> Bedrock model, and emits the exact response
contract the frontend expects.

Keeps the hard separation required by the brief:
    data -> validation -> deterministic calculator -> structured result
    -> AI MAY explain (never compute, never override).

Sessions are kept in memory here (fine for the hackathon demo). Swapping
this for DynamoDB later means replacing SessionStore only — see the note
at the bottom. The rest of the flow is storage-agnostic.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from .calculator import run_needs_assessment, DISCLAIMER
from .models import Assessment
from .bedrock_service import BedrockService, Source
from .extractor import (extract_profile_updates, classify_intent,
                        extract_bare_amount)

# Required inputs that hold a money amount. A bare numeric reply ("About 85k.")
# is attributed to whichever of these the assistant asked about last.
_MONEY_FIELDS = {"annual_income", "mortgage_balance", "non_mortgage_debt",
                 "existing_coverage", "liquid_savings"}


class SessionStore:
    """In-memory session store. Replace with a DynamoDB-backed store later
    without touching the orchestrator logic (same get/save interface)."""

    def __init__(self) -> None:
        self._sessions: dict[str, Assessment] = {}

    def get_or_create(self, session_id: str | None) -> tuple[str, Assessment]:
        if session_id and session_id in self._sessions:
            return session_id, self._sessions[session_id]
        new_id = session_id or f"sess_{uuid.uuid4().hex[:12]}"
        assessment = Assessment()
        self._sessions[new_id] = assessment
        return new_id, assessment

    def save(self, session_id: str, assessment: Assessment) -> None:
        self._sessions[session_id] = assessment


class Orchestrator:
    def __init__(self, bedrock: BedrockService | None = None,
                 store: SessionStore | None = None) -> None:
        self.bedrock = bedrock or BedrockService()
        self.store = store or SessionStore()

    def handle_turn(
        self,
        *,
        session_id: str | None = None,
        message: str = "",
        profile_updates: dict | None = None,
        assumption_updates: dict | None = None,
    ) -> dict:
        """One conversational turn. Returns the response contract dict."""
        session_id, assessment = self.store.get_or_create(session_id)

        # 1. Apply any new data the frontend collected.
        if profile_updates:
            assessment.update(profile_updates)
        if assumption_updates:
            assessment.set_assumptions(assumption_updates)

        # 1b. Deterministic extraction of facts the customer stated in plain
        #     language. Conservative by design: only values actually spoken are
        #     recorded. Never invents an amount, and never touches the maths.
        extracted = extract_profile_updates(message)
        if extracted:
            assessment.update(extracted)

        intent = classify_intent(message)

        # 1c. Decide whether an assessment is actually wanted. Previously ANY
        #     message while status was "collecting" triggered the next intake
        #     question, so out-of-scope chatter started a life-insurance
        #     assessment. It now begins only when the user asks for it, or has
        #     volunteered information that clearly belongs to one.
        assessment_keys = {"annual_income", "num_children", "mortgage_balance",
                           "non_mortgage_debt", "existing_coverage", "liquid_savings"}
        if intent == "assessment" or (extracted and assessment_keys & set(extracted)):
            assessment.assessment_started = True

        # 1d. A bare figure answering the question we just asked. People reply
        #     "About 85k." rather than restating "I make ...", so the keyword
        #     extractors can't see it. Attribute it to the field we asked
        #     about -- the orchestrator knows that, the extractor never guesses.
        if assessment.assessment_started and not (extracted and assessment_keys & set(extracted)):
            pending = assessment.next_field()
            if pending and pending["key"] in _MONEY_FIELDS:
                bare = extract_bare_amount(message)
                if bare is not None:
                    assessment.update({pending["key"]: bare})
                    extracted = {**(extracted or {}), pending["key"]: bare}

        # 2. Deterministic calculation (only when we have the required inputs).
        needs_block = _empty_needs_block()
        calc_context = ""
        status = assessment.status()
        if status == "ready":
            result = run_needs_assessment(assessment.profile, assessment.assumptions)
            # Only the transparent DIME needs assessment is published.
            # premium_estimate is deliberately NOT exposed: LifeLine does not
            # quote prices. human_life_value / sanity_check / flags stay
            # internal for now and are not part of the customer-facing
            # contract.
            needs_block = {
                "illustrative_gap": result.illustrative_gap,
                "breakdown": {
                    "components": result.breakdown,
                    "offsets": result.offset_lines,
                    "gross_need": result.gross_need,
                    "total_offsets": result.offsets,
                },
                "assumptions": result.assumptions,
                "disclaimer": result.disclaimer,
            }
            calc_context = result.explanation

        # 3. Retrieve Lincoln educational content (empty if KB not configured).
        #    Skipped while collecting an assessment: retrieval is what pulled
        #    the model toward writing an article instead of asking a question.
        collecting = status == "collecting" and assessment.assessment_started
        sources: list[Source] = []
        if message and (not collecting or intent == "educational"):
            sources = self.bedrock.retrieve_knowledge(message)

        next_field = assessment.next_field() if collecting else None

        # 4. Decide the mode. The APPLICATION decides this, not the model.
        guardrail = intent if intent in ("pricing", "recommendation", "approval") else None
        if intent == "out_of_scope":
            # Out of scope wins over everything else: never open an assessment.
            mode = "out_of_scope"
        elif collecting and intent == "educational":
            # A genuine educational question mid-assessment: answer briefly,
            # then return to the pending question.
            mode = "answering_then_resuming"
        elif collecting:
            mode = "collecting"
        else:
            mode = "explaining"

        assistant_message = self.bedrock.generate_grounded_response(
            query=message, context=calc_context, sources=sources, mode=mode,
            next_field=next_field, known_summary=assessment.known_summary(),
            guardrail=guardrail,
        )

        # 5. Deterministic fallbacks. These must produce a usable turn even when
        #    Bedrock is unconfigured or returns nothing.
        if not assistant_message:
            if mode == "out_of_scope":
                assistant_message = _out_of_scope_fallback(intent, message)
            elif guardrail:
                assistant_message = _guardrail_fallback(guardrail)
                if collecting and next_field:
                    assistant_message += "\n\n" + _ask(next_field)
            elif mode == "collecting" and next_field:
                assistant_message = _collecting_prompt(assessment, next_field)
            elif mode == "answering_then_resuming" and next_field:
                assistant_message = (_educational_fallback(assessment)
                                     + "\n\n" + _ask(next_field))
            else:
                assistant_message = _ready_prompt(needs_block)

        # 6. Assemble the response contract.
        assessment_block = assessment.to_dict()
        # Don't advertise a pending intake question while no assessment is
        # actually running -- otherwise the UI is told to prompt for a field
        # even though we just told the user we aren't starting one.
        if not assessment.assessment_started:
            assessment_block["next_field"] = None
            assessment_block["next_field_question"] = None
            assessment_block["next_field_why"] = None
        return {
            "session_id": session_id,
            "assistant_message": assistant_message,
            "mode": mode,
            "intent": intent,
            "extracted": extracted,
            "assessment_started": assessment.assessment_started,
            "assessment": {
                "status": assessment_block["status"],
                "assessment_started": assessment_block["assessment_started"],
                "missing_fields": assessment_block["missing_fields"],
                "next_field": assessment_block["next_field"],
                "next_field_question": assessment_block["next_field_question"],
                "next_field_why": assessment_block["next_field_why"],
                "known_summary": assessment_block["known_summary"],
                "profile": assessment_block["profile"],
                "context": assessment_block["context"],
                "assumptions": assessment_block["assumptions"],
                "field_help": assessment_block["field_help"],
            },
            "needs_assessment": needs_block,
            "sources": [s.to_dict() for s in sources],
            "disclaimer": DISCLAIMER,
        }


def _empty_needs_block() -> dict:
    return {"illustrative_gap": None, "breakdown": {}, "assumptions": {}}


def _ask(next_field: dict) -> str:
    return next_field["question"]


def _collecting_prompt(assessment: Assessment, next_field: dict) -> str:
    """Deterministic guided-assessment turn used when the model is unavailable.

    Still leads the conversation and still asks for exactly one thing. The
    longer preamble is used only on the opening turn so the reply does not
    repeat itself on every question.
    """
    known = assessment.known_summary()
    if not known:
        return ("Absolutely — I can walk you through an illustrative needs "
                "assessment. We'll go one question at a time.\n\n" + _ask(next_field))
    return f"Thanks — got it. So far I have: {known}.\n\n{_ask(next_field)}"


def _ready_prompt(needs: dict | None = None) -> str:
    """Explain the estimate conversationally, using the calculator's own output.

    Every figure here comes from `needs` (the deterministic calculator). Nothing
    is recomputed or invented.
    """
    if not needs or needs.get("illustrative_gap") is None:
        return ("Thanks — I have everything I need. Your illustrative estimate is "
                "shown below.")

    gap = needs["illustrative_gap"]
    breakdown = needs.get("breakdown", {})
    gross = breakdown.get("gross_need")
    offsets = breakdown.get("total_offsets")
    components = sorted(
        (c for c in breakdown.get("components", []) if c["amount"] > 0),
        key=lambda c: c["amount"], reverse=True)

    parts = ["Thanks — I have what I need.",
             f"Based on the information you've provided, the illustrative estimate "
             f"is approximately ${gap:,} of additional coverage."]

    if components:
        biggest = components[0]
        others = [c for c in components[1:]]
        listed = ", ".join(c["label"].lower() for c in others)
        why = f"The largest component is {biggest['label'].lower()} at " \
               f"${biggest['amount']:,}"
        if biggest.get("detail"):
            why += f" ({biggest['detail']})"
        why += "."
        if others:
            why += f" The estimate also includes {listed}."
        parts.append(why)

    if offsets:
        parts.append(
            f"Your existing coverage and savings reduce the remaining gap, and "
            f"you can see every line in the breakdown below.")
    else:
        parts.append("There are no existing resources offsetting this yet.")

    parts.append(
        "This is an illustration based on the assumptions I listed, not a quote "
        "or a recommendation. Happy to explain any part, or talk through term "
        "versus permanent approaches.")
    return " ".join(parts)


def _out_of_scope_fallback(intent: str, message: str) -> str:
    """Politely restate scope. Never starts an assessment."""
    topic = ""
    match = re.search(
        r"\b(health insurance|medical insurance|dental|vision insurance|"
        r"car insurance|auto insurance|vehicle insurance|home insurance|"
        r"renters insurance|renters'?|homeowners|travel insurance|"
        r"disability insurance|long[- ]term care insurance|pet insurance)\b",
        message or "", re.IGNORECASE)
    if match:
        topic = match.group(0).lower()
    lead = (f"LifeLine focuses on life insurance rather than {topic}. "
            if topic else "LifeLine focuses on life insurance. ")
    return (lead + "I can help you understand life-insurance options, or work "
            "through an illustrative estimate of your family's protection needs. "
            "Would either of those be useful?")


def _educational_fallback(assessment: Assessment) -> str:
    return ("That's a good question, and I'll explain it properly once we're "
            "through these few questions.")


_GUARDRAILS = {
    "pricing": ("Lifeline can show an illustrative needs assessment — an estimate "
                "based on the information you give me — but it is not a quote and "
                "it cannot give you a price. Real premiums come from a licensed "
                "insurer after underwriting, and a licensed adviser can get you "
                "actual figures."),
    "recommendation": ("I can't recommend a policy or tell you what to buy — that "
                       "would need a licensed adviser who can assess you properly. "
                       "What I can do is walk you through the tradeoffs between term "
                       "and permanent cover once we've built your illustrative "
                       "estimate, so the choice is easier to discuss."),
    "approval": ("Lifeline can't approve anyone, check eligibility, or do "
                 "underwriting. Only a licensed insurer can do that, after you give "
                 "them your details."),
}


def _guardrail_fallback(kind: str) -> str:
    return _GUARDRAILS.get(kind, "")


# ---------------------------------------------------------------------------
# NOTE on scaling / "a file may be insecure":
# Customer profile data lives in SessionStore (in memory here). For a scalable,
# secure deployment, replace SessionStore with a DynamoDB-backed implementation
# (same get_or_create / save interface). DynamoDB gives per-item encryption,
# access control, and horizontal scale. The deterministic calculator, models,
# and Bedrock service do not change. Do NOT persist customer PII to flat files.
# ---------------------------------------------------------------------------
