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

        # 2. Deterministic calculation (only when we have the required inputs).
        needs_block = _empty_needs_block()
        calc_context = ""
        status = assessment.status()
        if status == "ready":
            result = run_needs_assessment(assessment.profile, assessment.assumptions)
            needs_block = {
                "illustrative_gap": result.illustrative_gap,
                "breakdown": {
                    "components": result.breakdown,
                    "offsets": result.offset_lines,
                    "gross_need": result.gross_need,
                    "total_offsets": result.offsets,
                    "sanity_check": result.sanity_check,
                    "human_life_value": result.human_life_value,
                    "premium_estimate": result.premium_estimate,
                    "flags": result.flags,
                },
                "assumptions": result.assumptions,
                "disclaimer": result.disclaimer,
            }
            calc_context = result.explanation

        # 3. Retrieve Lincoln educational content (empty if KB not configured).
        sources: list[Source] = self.bedrock.retrieve_knowledge(message) if message else []

        # 4. AI explanation (grounded). Falls back to the calculator's own
        #    plain-language explanation when Bedrock is not configured.
        assistant_message = self.bedrock.generate_grounded_response(
            query=message, context=calc_context, sources=sources
        )
        if not assistant_message:
            assistant_message = calc_context or _collecting_prompt(assessment)

        # 5. Assemble the response contract.
        assessment_block = assessment.to_dict()
        return {
            "session_id": session_id,
            "assistant_message": assistant_message,
            "assessment": {
                "status": assessment_block["status"],
                "missing_fields": assessment_block["missing_fields"],
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


def _collecting_prompt(assessment: Assessment) -> str:
    missing = assessment.missing_fields()
    if missing:
        return (
            "To put together an illustrative needs assessment, I just need a few "
            "details. Could you tell me your annual income to start?"
        )
    return "Thanks — I have what I need. Would you like me to walk through your estimate?"


# ---------------------------------------------------------------------------
# NOTE on scaling / "a file may be insecure":
# Customer profile data lives in SessionStore (in memory here). For a scalable,
# secure deployment, replace SessionStore with a DynamoDB-backed implementation
# (same get_or_create / save interface). DynamoDB gives per-item encryption,
# access control, and horizontal scale. The deterministic calculator, models,
# and Bedrock service do not change. Do NOT persist customer PII to flat files.
# ---------------------------------------------------------------------------
