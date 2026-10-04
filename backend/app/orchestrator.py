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
                        wants_to_skip, is_uncertain, wants_comparison,
                        wants_all_options)
from . import policy_records
from . import product_matcher
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
        path: str | None = None,
    ) -> dict:
        """One conversational turn. Returns the response contract dict."""
        session_id, assessment = self.store.get_or_create(session_id)

        # The homepage sends which journey the customer chose, and they can
        # switch it later. It persists on the session and resets with a new
        # chat (a fresh session has no path).
        if path is not None:
            assessment.set_path(path)
        active_path = assessment.path
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
            # A clear answer resets any unclear-attempt counters for those fields.
            for key in extracted:
                assessment.clear_unclear(key)

        # 1c. Skip / "I don't know" handling, and unclear-answer escalation.
        #     If the user is answering the field we just asked about but gave
        #     neither a usable value nor an explicit zero, we must NOT ask the
        #     same question again verbatim forever. We either skip the field
        #     (leaving it UNKNOWN, never zero) when they say so, or escalate to
        #     a clarification that offers a Skip / Not-sure option.
        skipped_now = None
        clarify_field = None
        if asked_field and asked_field not in extracted and intent not in (
                "educational", "pricing", "recommendation", "approval"):
            if wants_to_skip(message):
                # Explicit "skip" / "move on": honor immediately. Field stays
                # unknown (never zero) and we advance.
                assessment.skip_field(asked_field)
                skipped_now = asked_field
                assessment.clear_unclear(asked_field)
            elif is_uncertain(message):
                # "I don't know" / "no idea": don't skip yet and don't repeat
                # the identical question -- offer a reworded prompt with an
                # explicit Skip option. If uncertainty persists, skip it.
                attempts = assessment.note_unclear(asked_field)
                if attempts >= 2:
                    assessment.skip_field(asked_field)
                    skipped_now = asked_field
                    assessment.clear_unclear(asked_field)
                else:
                    clarify_field = asked_field
            elif message.strip():
                # The user said something, but we couldn't read an answer for
                # the field in play. One unclear reply gets a clearer question.
                # A second one skips the field so the conversation cannot loop.
                # The skip stays unknown and does not become zero.
                attempts = assessment.note_unclear(asked_field)
                if attempts >= 2:
                    assessment.skip_field(asked_field)
                    skipped_now = asked_field
                    assessment.clear_unclear(asked_field)
                else:
                    clarify_field = asked_field

        # Decide whether we are in an assessment. We only collect financial
        # details once the user has entered one — by asking for an estimate,
        # by giving a financial fact, or by having started earlier. A purely
        # general or educational question on a fresh profile does NOT begin
        # collecting.
        #
        # The "policy" path (help understanding an EXISTING policy) must never
        # auto-start new-customer financial intake, even if the user mentions a
        # number. Only an explicit switch to the coverage path (or the frontend
        # sending profile_updates) begins collection.
        if active_path == "policy":
            pass  # existing-policy help: no financial intake
        elif active_path == "coverage" or intent == "assessment" or extracted or profile_updates:
            assessment.mark_started()

        # 2. Deterministic calculation (only when we have the required inputs).
        needs_block = _empty_needs_block()
        recommendation_block = None
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

            # 2b. Deterministic product match (new-customer path only). This
            #     turns the raw calculator stats into ONE suggested catalog
            #     product plus plain-language reasons, so the conclusion can be
            #     presented as a recommendation rather than a list of figures.
            #     The matcher never computes coverage, never quotes a price,
            #     and never pushes a product when no additional cover is needed.
            #     The existing-policy path never reaches 'ready', but we gate on
            #     it explicitly so this can only ever fire for a new customer.
            if active_path != "policy":
                recommendation_block = _build_recommendation(
                    profile=assessment.profile,
                    calculator_result=needs_block,
                    context=assessment.context,
                )

        # 3. Retrieve Lincoln educational content (empty if KB not configured).
        #    Skipped while collecting an assessment: retrieval is what pulled
        #    the model toward writing an article instead of asking a question.
        collecting = status == "collecting" and assessment.started
        sources: list[Source] = []
        if message and (not collecting or intent == "educational"):
            sources = self.bedrock.retrieve_knowledge(message)

        next_field = assessment.next_field() if collecting else None

        # 4. Decide the mode. The APPLICATION decides this, not the model.
        guardrail = intent if intent in ("pricing", "recommendation", "approval") else None
        if intent == "out_of_scope":
            mode = "out_of_scope"
        elif collecting and intent == "educational":
            # A genuine educational question mid-assessment: answer briefly,
            # then return to the pending question.
            mode = "answering_then_resuming"
        elif collecting:
            mode = "collecting"
        elif active_path == "policy":
            # Helping someone understand an EXISTING policy: explain, don't
            # collect, and don't push a new-customer assessment.
            mode = "policy"
        else:
            mode = "explaining"

        # 4b. Policy-record lookup. If the customer mentions a demo policy id
        #     (strict DEMO-... form; a bare policy number never matches), we
        #     explain THEIR actual record deterministically and grounded in it,
        #     citing the source document. Unknown ids get a useful reply that
        #     invents nothing. This takes priority over the model so the
        #     explanation stays faithful to the record and is never paraphrased
        #     into something the record doesn't say.
        policy_lookup = None
        looked_up_id = policy_records.find_policy_id_in_text(message)
        if looked_up_id:
            record = policy_records.get_record(looked_up_id)
            if record is not None:
                policy_lookup = {"status": "found", "id": record["id"],
                                 "product_id": record.get("product_id")}
                assistant_message = policy_records.explain_record(record)
            else:
                policy_lookup = {"status": "unknown", "id": looked_up_id}
                assistant_message = policy_records.unknown_id_message(looked_up_id)

        # 4c. Follow-up comparisons. Once an estimate (and therefore a
        #     recommendation) exists on the new-customer path, the customer can
        #     ask "what are my other options?", "why not whole life?", or
        #     "compare these". We answer deterministically from the catalog so
        #     nothing about a product is invented, surfacing only the most
        #     relevant alternatives first. This never fires on the policy path
        #     or before an estimate exists, so it can't derail intake.
        comparison_block = None
        if (policy_lookup is None and active_path != "policy"
                and status == "ready" and message
                and wants_comparison(message)):
            resolved = product_matcher.resolve_product_query(message)
            focus_id = resolved.get("product_id")
            comparison = product_matcher.compare_products(
                profile=assessment.profile,
                calculator_result=needs_block,
                context=assessment.context,
                focus_product_id=focus_id,
                include_all=wants_all_options(message),
            )
            comparison_block = {
                "resolved_query": resolved,
                **comparison,
            }
            assistant_message = _compose_comparison_message(comparison, resolved)

        # When we must clarify a repeatedly-unclear answer, the application
        # owns the wording deterministically (offering a Skip option) rather
        # than letting the model echo the same question again.
        if policy_lookup is not None:
            pass  # deterministic record reply already set above
        elif comparison_block is not None:
            pass  # deterministic comparison reply already set above
        elif clarify_field and next_field and next_field["key"] == clarify_field:
            assistant_message = _clarify_prompt(next_field)
        elif skipped_now and next_field:
            assistant_message = (
                "No problem. I'll leave that blank, and I won't count a blank as zero.\n\n"
                + _ask(next_field))
        elif (assessment.started and status != "ready" and not next_field
              and assessment.unanswered_required()):
            assistant_message = _incomplete_prompt(assessment)
        else:
            assistant_message = self.bedrock.generate_grounded_response(
                query=message, context=calc_context, sources=sources, mode=mode,
                next_field=next_field, known_summary=assessment.known_summary(),
                guardrail=guardrail,
                memories=memory_context,
            )
            # If we just skipped a field, make sure the reply acknowledges it
            # and moves on to the next question rather than silently jumping.
            if skipped_now and next_field and not assistant_message:
                pass  # handled by the fallback block below

        # 5. Deterministic fallbacks. These must produce a usable turn even when
        #    Bedrock is unconfigured or returns nothing.
        if not assistant_message:
            if skipped_now and next_field:
                assistant_message = (
                    "No problem. I'll leave that blank, and I won't count a blank as zero.\n\n"
                    + _ask(next_field))
            elif (assessment.started and status != "ready" and not next_field
                  and assessment.unanswered_required()):
                assistant_message = _incomplete_prompt(assessment)
            elif mode == "out_of_scope":
                assistant_message = _out_of_scope_fallback(message)
            elif guardrail:
                assistant_message = _guardrail_fallback(guardrail)
                if collecting and next_field:
                    assistant_message += "\n\n" + _ask(next_field)
            elif mode == "collecting" and next_field:
                assistant_message = _collecting_prompt(assessment, next_field)
            elif mode == "answering_then_resuming" and next_field:
                assistant_message = (_educational_fallback(assessment)
                                     + "\n\n" + _ask(next_field))
            elif status == "ready":
                assistant_message = calc_context or _ready_prompt(needs_block)
            elif mode == "policy":
                assistant_message = _policy_fallback(bool(message))
            else:
                # Idle / general question with no estimate yet.
                assistant_message = _general_fallback()

        # Remember what we asked about this turn, so the next message's bare
        # answer attaches to it. Clear it when not actively collecting.
        if next_field:
            assessment.set_last_asked(next_field["key"])
        elif assessment.started and assessment.unanswered_required():
            # The next bare number fills the first blank, which may be a skip.
            assessment.set_last_asked(assessment.unanswered_required()[0])
        else:
            assessment.set_last_asked(None)

        # 6. Persist the session. A failed save must not break the turn.
        #    submit_for_review checks the boolean and will not claim success.
        self.store.save(session_id, assessment)

        # 7. Assemble the response contract.
        assessment_block = assessment.to_dict()
        if not assessment.started:
            assessment_block["next_field"] = None
            assessment_block["next_field_question"] = None
            assessment_block["next_field_why"] = None
        return {
            "session_id": session_id,
            "assistant_message": assistant_message,
            "mode": mode,
            "intent": intent,
            "policy_lookup": policy_lookup,
            "path": active_path,
            "extracted": extracted,
            "assessment_started": assessment.started,
            "assessment": {
                # Report "idle" until an assessment has actually been entered,
                # so a general question never shows a collection UI.
                "status": status if status == "idle" else assessment_block["status"],
                "assessment_started": assessment.started,
                "missing_fields": assessment_block["missing_fields"],
                # next_field is only meaningful while actively collecting; when
                # idle/policy/ready there is no question pending, so report None
                # rather than the raw first-missing field.
                "next_field": next_field["key"] if next_field else None,
                "next_field_question": next_field["question"] if next_field else None,
                "next_field_why": next_field["why"] if next_field else None,
                "known_summary": assessment_block["known_summary"],
                "profile": assessment_block["profile"],
                "context": assessment_block["context"],
                "assumptions": assessment_block["assumptions"],
                "field_help": assessment_block["field_help"],
                # Any field being asked can be skipped; the frontend can show a
                # "Skip / Not sure" quick reply. Skipped fields stay unknown.
                "can_skip": bool(next_field),
                "skipped_fields": sorted(assessment.skipped_fields),
            },
            "skipped": skipped_now,
            "needs_assessment": needs_block,
            # Structured, inspectable product recommendation for the new-customer
            # path. None unless an estimate is ready on the coverage path. The
            # frontend renders this as the conclusion; the detailed calculator
            # math moves into an expandable section.
            "recommendation": recommendation_block,
            # Structured, catalog-grounded comparison of alternatives. Non-null
            # only when the customer asked to compare / see other options / why
            # not a named product, and an estimate already exists.
            "comparison": comparison_block,
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

        saved = self.store.save(
            session_id, assessment, status="pending_review",
            meta={"reference": reference, "contact": contact})
        if not saved:
            return {
                "ok": False,
                "error": "Your details were not saved and were not sent.",
                "advisor_notified": False,
            }

        notified = self._notify_advisor(reference, assessment, contact)
        if notified:
            message = (
                f"Your details are saved and a licensed advisor was notified. "
                f"Your reference number is {reference}."
            )
        else:
            message = (
                f"Your details are saved for review. An advisor was not notified. "
                f"Your reference number is {reference}."
            )
        return {
            "ok": True,
            "reference": reference,
            "status": "pending_review",
            "advisor_notified": notified,
            "message": message,
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


def _compose_comparison_message(comparison: dict, resolved: dict) -> str:
    """Build a warm, plain-language comparison reply, strictly from the
    structured (catalog-grounded) comparison. Covers, per alternative: what it
    does, its benefits and main tradeoff, why it fits the stated needs less
    closely, and what change in priorities would make it a better fit. Never
    invents a product or a fact; acknowledges a named product that isn't in the
    catalog, and flags undocumented details rather than guessing them."""
    lines: list[str] = []

    # A named product that isn't in the demo catalog ("why not whole life?"):
    # acknowledge the gap honestly before showing what we do model.
    if resolved.get("named_term") and not resolved.get("in_catalog"):
        note = resolved.get("note") or (
            f"\"{resolved['named_term']}\" isn't one of the demo options I can "
            "compare here.")
        lines.append(note)
        lines.append("")

    current = comparison.get("current_suggestion")
    if comparison.get("no_additional_coverage"):
        lines.append("Right now your assessment doesn't point to a need for more "
                     "cover, so there isn't a product to put forward — but here's "
                     "how the demo options differ, in case it's useful.")
        lines.append("")
    elif current:
        lines.append(f"The option I suggested is **{current['name']}**. Here's how "
                     "the other demo options compare, so you can see the tradeoffs "
                     "for yourself.")
        lines.append("")

    alternatives = comparison.get("alternatives") or []
    if not alternatives:
        lines.append("There aren't other demo options to compare beyond the one "
                     "suggested.")
        return "\n".join(lines).strip()

    for alt in alternatives:
        lines.append(f"**{alt['name']}**")
        if alt.get("what_it_does"):
            lines.append(alt["what_it_does"])
        benefits = alt.get("benefits") or []
        if benefits:
            lines.append("What it offers: " + "; ".join(benefits))
        if alt.get("primary_tradeoff"):
            lines.append("Main tradeoff: " + alt["primary_tradeoff"])
        less = alt.get("fits_less_closely_because") or []
        if less:
            lines.append("Why it fits your current needs less closely: " + less[0])
        better = alt.get("would_fit_better_if") or []
        if better:
            # Phrase as a priority change the customer could make.
            lines.append("It could be the better choice if " + better[0]
                         .replace("it would fit better if ", "") + ".")
        undocumented = alt.get("undocumented") or []
        if undocumented:
            lines.append("_Note: the demo material doesn't document "
                         + _humanise_fields(undocumented)
                         + " for this option, so I can't speak to those._")
        lines.append("")

    if comparison.get("has_more"):
        remaining = comparison["alternatives_total"] - len(alternatives)
        lines.append(f"There {'is' if remaining == 1 else 'are'} {remaining} more "
                     f"demo option{'' if remaining == 1 else 's'} I can walk through "
                     "— just say \"show me all my options\".")
        lines.append("")

    lines.append("None of this is a quote or a recommendation to buy. Tell me if "
                 "your priorities have changed and I'll reconsider which fits best, "
                 "or I can connect you with a licensed advisor.")
    return "\n".join(lines).strip()


# Friendly names for the catalog's "unknown" field keys, so a comparison can
# say what is undocumented in plain words rather than exposing raw keys.
_FIELD_HUMAN = {
    "eligibility": "who qualifies",
    "availability": "where it's available",
    "costs.premium_detail": "actual pricing",
    "costs.relative_cost": "relative cost",
    "optional_riders": "optional add-ons",
}


def _humanise_fields(fields: list[str]) -> str:
    names = [_FIELD_HUMAN.get(f, f.replace("_", " ").replace(".", " ")) for f in fields]
    # De-duplicate while preserving order (relative_cost + premium_detail both
    # map toward pricing-ish phrases but stay distinct here).
    seen: list[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    if len(seen) == 1:
        return seen[0]
    if len(seen) == 2:
        return f"{seen[0]} or {seen[1]}"
    return ", ".join(seen[:-1]) + f", or {seen[-1]}"


# Benefits/limitations in the catalog are written for a general audience; we
# surface at most this many so the recommendation card stays scannable. The
# frontend may show fewer, but never invents extras.
_MAX_BENEFITS = 3
_MAX_LIMITATIONS = 1


def _build_recommendation(*, profile: dict, calculator_result: dict,
                          context: dict) -> dict:
    """Turn the deterministic match into a presentation-ready block.

    The matcher owns the DECISION (which product, why, provisional or not,
    whether any cover is needed). Here we only attach the chosen product's
    plain-language display fields from the catalog so the frontend can render a
    recommendation without re-deriving anything. We invent nothing: product
    name, explanation, benefits, duration and limitation all come straight from
    the catalog entry, and pricing is always reported as unavailable (LifeLine
    never quotes a price).
    """
    match = product_matcher.match_product(
        profile=profile,
        calculator_result=calculator_result,
        context=context,
    ).to_dict()

    product_id = match.get("suggested_product_id")
    product = product_matcher._policy(product_id) if product_id else None

    display = None
    if product:
        benefits = product.get("benefits") or []
        # Prefer the catalog's own curated limitations; fall back to the
        # matcher's if the entry had none. Either way we show one tradeoff so
        # the recommendation is honest about the downside.
        limitations = product.get("limitations") or match.get("limitations") or []
        duration = product.get("coverage_period")
        # Only present a duration when the product actually defines a concrete
        # one AND the user's need supports it (a stated term for the term
        # products). Permanent products describe duration as conditional, so we
        # pass the catalog text through rather than implying a fixed span.
        display = {
            "product_id": product_id,
            "name": product.get("display_name") or product.get("policy_type") or product_id,
            "policy_type": product.get("policy_type"),
            "plain_language": product.get("plain_language") or product.get("description"),
            "benefits": list(benefits)[:_MAX_BENEFITS],
            "primary_limitation": (list(limitations)[:_MAX_LIMITATIONS] or [None])[0],
            "coverage_duration": duration,
            "tradeoffs": product.get("tradeoffs"),
            "demo_disclaimer": product.get("demo_disclaimer"),
        }

    return {
        # Raw, inspectable matcher output (decision + reasons + guardrail flags).
        "match": match,
        # Catalog-sourced display fields for the suggested product (or None when
        # no product is suggested, e.g. insufficient evidence or no need).
        "product": display,
        # LifeLine never quotes a price. The frontend shows pricing as
        # unavailable until a real quote exists (via a licensed advisor).
        "pricing": {
            "available": False,
            "message": "A price isn't available here. A licensed advisor can turn "
                       "this illustration into a real quote.",
        },
        # Presentation hint: a provisional match is shown as "An option to
        # consider", a confident one as a recommendation.
        "preliminary": bool(match.get("provisional")) or product_id is None,
    }


_FIELD_LABELS = {
    "annual_income": "income",
    "num_children": "dependents",
    "mortgage_balance": "the mortgage",
    "non_mortgage_debt": "other debt",
    "existing_coverage": "existing coverage",
    "liquid_savings": "savings",
}


def _incomplete_prompt(assessment: Assessment) -> str:
    """Say which answers are still blank. A blank is not a zero."""
    names = [_FIELD_LABELS.get(key, key.replace("_", " "))
             for key in assessment.unanswered_required()]
    if len(names) == 1:
        listed = names[0]
    elif len(names) == 2:
        listed = f"{names[0]} and {names[1]}"
    else:
        listed = ", ".join(names[:-1]) + f", and {names[-1]}"
    return (
        f"I left {listed} blank. A blank is not zero, so I can't finish the "
        "estimate until you give me a number. A rough guess is fine."
    )


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


def _ready_prompt(needs: dict | None = None) -> str:
    gap = (needs or {}).get("illustrative_gap")
    if gap is None:
        return ("Thanks — I have everything I need. Your illustrative coverage estimate "
                "is shown below, and you can change any answer to recalculate.")
    breakdown = needs.get("breakdown", {})
    components = sorted(
        (row for row in breakdown.get("components", []) if row.get("amount", 0) > 0),
        key=lambda row: row["amount"], reverse=True)
    message = ("Thanks — I have what I need. Based on what you've shared, your "
               f"illustrative additional coverage estimate is about ${gap:,}.")
    if components:
        largest = components[0]
        message += (f" The largest component is {largest['label'].lower()} at "
                    f"${largest['amount']:,}.")
    message += (" Your existing resources reduce the gap; the breakdown below shows "
                "each part. This is an illustration based on the assumptions shown, "
                "not a quote or recommendation.")
    return message


def _clarify_prompt(next_field: dict) -> str:
    """Reword a field's question after a couple of unclear replies, and
    explicitly offer a way out so the customer is never stuck on a loop."""
    return (
        f"Sorry, I didn't quite catch that. {next_field['question']}\n\n"
        "A rough number is fine — or just say \"skip\" or \"I'm not sure\" and "
        "we'll move on and leave it blank."
    )


def _general_fallback() -> str:
    return ("Happy to help. You can ask me about how life insurance works, or "
            "say \"estimate my coverage\" and I'll put together a quick coverage "
            "estimate with you — one question at a time.")


def _policy_fallback(has_message: bool) -> str:
    if not has_message:
        return ("Of course — let's make sense of the policy you already have. "
                "You can tell me what you'd like to understand (for example what "
                "it covers, who receives the money, or what a term on it means), "
                "or read out any line you're unsure about and I'll explain it in "
                "plain words. I won't ask you to start a new quote.")
    return ("Happy to help you understand your existing policy. Tell me which "
            "part you'd like explained — what's covered, what you pay, who the "
            "beneficiary is, or any wording that's unclear — and I'll put it in "
            "plain words. You can ask for a real person at any time.")


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


def _out_of_scope_fallback(message: str) -> str:
    match = re.search(
        r"\b(health insurance|medical insurance|dental|vision insurance|car insurance|"
        r"auto insurance|vehicle insurance|home insurance|renters insurance|travel insurance|"
        r"disability insurance|long[- ]term care insurance|pet insurance)\b",
        message or "", re.IGNORECASE)
    topic = f" rather than {match.group(0).lower()}" if match else ""
    return (f"LifeLine focuses on life insurance{topic}. I can help explain life-insurance "
            "options or work through an illustrative estimate of your family's protection needs. "
            "Would either be useful?")


# ---------------------------------------------------------------------------
# NOTE on scaling / "a file may be insecure":
# Customer profile data lives in SessionStore (in memory here). For a scalable,
# secure deployment, replace SessionStore with a DynamoDB-backed implementation
# (same get_or_create / save interface). DynamoDB gives per-item encryption,
# access control, and horizontal scale. The deterministic calculator, models,
# and Bedrock service do not change. Do NOT persist customer PII to flat files.
# ---------------------------------------------------------------------------
