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

import uuid
from dataclasses import dataclass, field

from .calculator import run_needs_assessment, DISCLAIMER
from .models import Assessment
from .bedrock_service import BedrockService, Source
from .extractor import extract_profile_updates, classify_intent


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
        collecting = status == "collecting"
        sources: list[Source] = []
        if message and (not collecting or intent == "educational"):
            sources = self.bedrock.retrieve_knowledge(message)

        next_field = assessment.next_field() if collecting else None

        # 4. Decide the mode. The APPLICATION decides this, not the model.
        guardrail = intent if intent in ("pricing", "recommendation", "approval") else None
        if collecting and intent == "educational":
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
            if guardrail:
                assistant_message = _guardrail_fallback(guardrail)
                if collecting and next_field:
                    assistant_message += "\n\n" + _ask(next_field)
            elif mode == "collecting" and next_field:
                assistant_message = _collecting_prompt(assessment, next_field)
            elif mode == "answering_then_resuming" and next_field:
                assistant_message = (_educational_fallback(assessment)
                                     + "\n\n" + _ask(next_field))
            else:
                assistant_message = calc_context or _ready_prompt()

        # 6. Assemble the response contract.
        assessment_block = assessment.to_dict()
        return {
            "session_id": session_id,
            "assistant_message": assistant_message,
            "mode": mode,
            "intent": intent,
            "extracted": extracted,
            "assessment": {
                "status": assessment_block["status"],
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


def _ready_prompt() -> str:
    return ("Thanks — I have everything I need. Your illustrative estimate is "
            "shown below, and you can change any answer and I will recalculate.")


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
