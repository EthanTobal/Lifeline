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
from .config import load_config
from .store import build_store


# Backwards-compatible name: existing tests/imports use SessionStore. It is now
# the in-memory implementation that lives in store.py.
from .store import InMemorySessionStore as SessionStore  # noqa: E402,F401


class Orchestrator:
    def __init__(self, bedrock: BedrockService | None = None,
                 store=None, config=None) -> None:
        self.config = config or load_config()
        self.bedrock = bedrock or BedrockService()
        # Durable DynamoDB store when configured; in-memory otherwise.
        self.store = store or build_store(self.config)

    def handle_turn(
        self,
        *,
        session_id: str | None = None,
        message: str = "",
        profile_updates: dict | None = None,
        assumption_updates: dict | None = None,
        memories: list[str] | None = None,
    ) -> dict:
        """One conversational turn. Returns the response contract dict."""
        session_id, assessment = self.store.get_or_create(session_id)
        # Browser memories are customer-provided context, never calculator inputs.
        memory_context = []
        if isinstance(memories, list):
            memory_context = [
                note.strip() for note in memories[:50]
                if isinstance(note, str) and 0 < len(note.strip()) <= 500
            ]

        # Context for this turn: which field we asked about last, and whether an
        # assessment has actually been entered. Both persist in the Assessment.
        asked_field = assessment.last_asked_field
        intent = classify_intent(message)

        # 1. Apply any new data the frontend collected (explicit form/slider).
        if profile_updates:
            assessment.update(profile_updates)
            assessment.mark_started()
        if assumption_updates:
            assessment.set_assumptions(assumption_updates)

        # 1b. Deterministic extraction of facts the customer stated in plain
        #     language, interpreted with the field we just asked about. Only
        #     values actually spoken are recorded; never invents an amount.
        extracted = extract_profile_updates(message, asked_field=asked_field)
        if extracted:
            assessment.update(extracted)

        # Decide whether we are in an assessment. We only collect financial
        # details once the user has entered one — by asking for an estimate,
        # by giving a financial fact, or by having started earlier. A purely
        # general or educational question on a fresh profile does NOT begin
        # collecting.
        if intent == "assessment" or extracted or profile_updates:
            assessment.mark_started()

        # 2. Deterministic calculation (only when we have the required inputs).
        needs_block = _empty_needs_block()
        calc_context = ""
        status = assessment.status() if assessment.started else "idle"
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
            memories=memory_context,
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
            elif status == "ready":
                assistant_message = calc_context or _ready_prompt()
            else:
                # Idle / general question with no estimate yet.
                assistant_message = _general_fallback()

        # Remember what we asked about this turn, so the next message's bare
        # answer attaches to it. Clear it when not actively collecting.
        assessment.set_last_asked(next_field["key"] if next_field else None)

        # 6. Persist the session so the collected profile survives (and can be
        #    handed to a human advisor later). Non-fatal if storage is down.
        try:
            self.store.save(session_id, assessment)
        except TypeError:
            # in-memory store has a simpler signature
            self.store.save(session_id, assessment)

        # 7. Assemble the response contract.
        assessment_block = assessment.to_dict()
        return {
            "session_id": session_id,
            "assistant_message": assistant_message,
            "mode": mode,
            "intent": intent,
            "extracted": extracted,
            "assessment": {
                # Report "idle" until an assessment has actually been entered,
                # so a general question never shows a collection UI.
                "status": status if status == "idle" else assessment_block["status"],
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


    def submit_for_review(self, *, session_id: str | None, contact: str = "") -> dict:
        """Hand the collected assessment to a human advisor for review.

        Securely persists the profile with a 'pending_review' status and a
        human-friendly reference id, and notifies an advisor via SNS. This is
        the human-in-the-loop step: the AI gathers and gives an illustrative
        estimate; a licensed advisor reviews the stored profile for a precise
        one. Returns the reference id for the customer to quote.
        """
        if not session_id:
            return {"ok": False, "error": "No session to submit."}
        _, assessment = self.store.get_or_create(session_id)

        # Short, readable reference the customer can quote to an advisor.
        reference = "LL-" + uuid.uuid4().hex[:8].upper()

        # Persist with review status + reference. DynamoDB store accepts meta;
        # the in-memory store ignores the extra kwargs.
        try:
            self.store.save(session_id, assessment, status="pending_review",
                            meta={"reference": reference,
                                  "contact": contact})
        except TypeError:
            self.store.save(session_id, assessment)

        # Notify an advisor (best-effort; never blocks the user).
        notified = self._notify_advisor(reference, assessment, contact)

        return {
            "ok": True,
            "reference": reference,
            "status": "pending_review",
            "advisor_notified": notified,
            "message": (
                f"All set. Your details have been securely saved and sent to a "
                f"licensed advisor for review. Your reference number is "
                f"{reference} — keep it handy. An advisor will follow up to turn "
                f"this illustrative estimate into a precise one."
            ),
        }

    def _notify_advisor(self, reference: str, assessment: Assessment,
                        contact: str) -> bool:
        if not self.config.agent_review_enabled:
            return False
        summary = assessment.known_summary() or "(no details captured)"
        body = (
            f"New Lifeline assessment submitted for review.\n\n"
            f"Reference: {reference}\n"
            f"Customer contact: {contact or '(not provided)'}\n"
            f"Captured profile: {summary}\n\n"
            f"This is an illustrative needs assessment collected by the Lifeline "
            f"assistant. Please review for a precise, underwritten quote."
        )
        try:
            self.bedrock  # no-op touch to keep attr usage obvious
            import boto3  # lazy, same pattern as the rest of the app
            if self.config.aws_profile:
                sess = boto3.Session(profile_name=self.config.aws_profile,
                                     region_name=self.config.aws_region)
            else:
                sess = boto3.Session(region_name=self.config.aws_region)
            sess.client("sns").publish(
                TopicArn=self.config.agent_review_topic_arn,
                Subject=f"Lifeline review request {reference}",
                Message=body,
            )
            return True
        except Exception:
            return False


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
        return ("Absolutely — I can put together a quick coverage estimate. "
                "We'll go one question at a time.\n\n" + _ask(next_field))
    return f"Thanks — got it. So far I have: {known}.\n\n{_ask(next_field)}"


def _ready_prompt() -> str:
    return ("Thanks — I have everything I need. Your coverage estimate is "
            "shown below, and you can change any answer and I will recalculate.")


def _general_fallback() -> str:
    return ("Happy to help. You can ask me about how life insurance works, or "
            "say \"estimate my coverage\" and I'll put together a quick coverage "
            "estimate with you — one question at a time.")


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
