"""Unit tests for the Lifeline backend.

Run offline — no AWS, no network. Verifies the deterministic calculator,
the assessment model, and the orchestration response contract.

    cd backend && python -m pytest ../tests -q
or from repo root:
    python -m pytest tests -q
"""
import json
import os
import sys

# Make `app` importable whether tests run from repo root or backend/.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.calculator import run_needs_assessment, resolve_assumptions, DEFAULT_ASSUMPTIONS
from app.models import Assessment
from app.orchestrator import Orchestrator, SessionStore
from app.bedrock_service import BedrockService


# A realistic family, matching the scenario validated earlier.
MARGARET = {
    "annual_income": 80000, "age": 40, "sex": "female", "smoker": False,
    "health": "good", "num_children": 2, "mortgage_balance": 200000,
    "non_mortgage_debt": 30000, "existing_coverage": 100000, "liquid_savings": 50000,
}


# ---------------- calculator ----------------

def test_dime_gross_and_gap():
    r = run_needs_assessment(MARGARET)
    # debt(30k+15k) + income(80k*10) + mortgage(200k) + education(2*100k)
    assert r.gross_need == 1_245_000
    assert r.offsets == 150_000          # 100k coverage + 50k savings
    assert r.illustrative_gap == 1_095_000


def test_breakdown_components_present():
    r = run_needs_assessment(MARGARET)
    keys = {b["key"] for b in r.breakdown}
    # final_expenses is its own visible line, not folded into debt.
    assert keys == {"debt", "final_expenses", "income", "mortgage", "education"}
    income_line = next(b for b in r.breakdown if b["key"] == "income")
    assert income_line["amount"] == 800_000


def test_breakdown_lines_sum_to_gross_need():
    """Every visible line item must add up to the published gross need."""
    r = run_needs_assessment(MARGARET)
    assert sum(b["amount"] for b in r.breakdown) == r.gross_need


def test_final_expenses_visible_separately():
    """The $15k final-expense assumption must be shown, not hidden in debt."""
    r = run_needs_assessment(MARGARET)
    final = next(b for b in r.breakdown if b["key"] == "final_expenses")
    assert final["amount"] == 15_000
    debt = next(b for b in r.breakdown if b["key"] == "debt")
    assert debt["amount"] == 30_000  # non-mortgage debt only
    assert final["amount"] + debt["amount"] == 45_000


def test_breakdown_detail_shows_the_arithmetic():
    """Each line explains its own derivation for the customer."""
    r = run_needs_assessment(MARGARET)
    detail = {b["key"]: b["detail"] for b in r.breakdown}
    assert "10 years" in detail["income"]        # 80,000 x 10
    assert "2 child" in detail["education"]      # 2 x 100,000


def test_sanity_band_and_within_flag():
    r = run_needs_assessment(MARGARET)
    assert r.sanity_check["low"] == 800_000
    assert r.sanity_check["high"] == 1_200_000
    # gross 1.245M is inside the band, so no "outside band" flag
    assert not any("outside the quick" in f for f in r.flags)


def test_hlv_present_and_reasonable():
    r = run_needs_assessment(MARGARET)
    assert r.human_life_value is not None
    assert r.human_life_value["years_to_retirement"] == 27  # 67 - 40
    # same ballpark as DIME (sanity, not exact)
    assert 1_000_000 <= r.human_life_value["value"] <= 1_600_000


def test_premium_estimate_reasonable():
    r = run_needs_assessment(MARGARET)
    assert r.premium_estimate is not None
    # healthy 40yo non-smoking woman, ~$1.1M, 20yr term -> tens of dollars/mo
    assert 40 <= r.premium_estimate["monthly"] <= 200


def test_smoker_costs_more():
    base = run_needs_assessment(MARGARET).premium_estimate["monthly"]
    smoker = run_needs_assessment({**MARGARET, "smoker": True}).premium_estimate["monthly"]
    assert smoker > base * 2  # smoker factor is 2.5x


def test_offsets_can_zero_out_need():
    rich = {**MARGARET, "existing_coverage": 2_000_000}
    r = run_needs_assessment(rich)
    assert r.illustrative_gap == 0
    assert any("already meet" in f for f in r.flags)


def test_missing_income_is_zero_not_crash():
    r = run_needs_assessment({"num_children": 1})
    assert r.gross_need >= 0  # does not raise, no invented income


# ---------------- assumptions: explicit, editable, no silent invention ----------------

def test_assumptions_are_returned():
    r = run_needs_assessment(MARGARET)
    assert r.assumptions["income_replacement_years"] == 10
    assert r.assumptions["education_per_child"] == 100_000


def test_editing_income_years_changes_result():
    base = run_needs_assessment(MARGARET).gross_need
    changed = run_needs_assessment(MARGARET, {"income_replacement_years": 15}).gross_need
    # 5 extra years * 80k = +400k
    assert changed - base == 400_000


def test_resolve_assumptions_merges_not_resets():
    merged = resolve_assumptions({"final_expenses": 20000})
    assert merged["final_expenses"] == 20000
    assert merged["education_per_child"] == DEFAULT_ASSUMPTIONS["education_per_child"]


# ---------------- assessment model ----------------

def test_status_collecting_then_ready():
    """Readiness requires every field the calculator actually consumes.

    The guided assessment needs a real profile before it can produce a
    defensible number, so all seven DIME/HLV inputs gate `ready`.
    """
    a = Assessment()
    assert a.status() == "collecting"
    assert set(a.missing_fields()) == {
        "annual_income", "num_children", "mortgage_balance",
        "non_mortgage_debt", "existing_coverage", "liquid_savings",
    }
    a.update({"annual_income": 60000})
    assert a.status() == "collecting"  # still short
    a.update({
        "num_children": 0, "mortgage_balance": 0, "non_mortgage_debt": 0,
        "existing_coverage": 0, "liquid_savings": 0,
    })
    assert a.status() == "ready"
    assert a.missing_fields() == []


def test_age_does_not_block_the_primary_assessment():
    """Age affects only the internal HLV and the unpublished premium, so it
    must not gate the customer-facing DIME result."""
    a = Assessment()
    a.update({
        "annual_income": 80000, "num_children": 2, "mortgage_balance": 180000,
        "non_mortgage_debt": 12000, "existing_coverage": 100000,
        "liquid_savings": 0,
    })
    assert a.status() == "ready"
    assert "age" not in a.missing_fields()
    assert a.next_field() is None


def test_next_field_follows_fixed_order():
    """The APPLICATION picks the next field, in a fixed order -- not the model."""
    a = Assessment()
    asked = []
    while a.status() == "collecting":
        field = a.next_field()
        assert field is not None
        asked.append(field["key"])
        assert field["question"]  # every field carries a plain-language question
        a.update({field["key"]: 0 if field["allows_zero"] else 1})
    assert asked == [
        "annual_income", "num_children", "mortgage_balance",
        "non_mortgage_debt", "existing_coverage", "liquid_savings",
    ]
    assert a.next_field() is None


def test_affordability_is_context_not_need():
    """Affordability must NOT change the calculated need (brief requirement)."""
    a = Assessment()
    a.update({"annual_income": 80000, "affordability_monthly": 50})
    # affordability routed to context, not profile
    assert "affordability_monthly" in a.context
    assert "affordability_monthly" not in a.profile
    # need with and without the affordability figure must be identical
    with_budget = run_needs_assessment(a.profile, a.assumptions).gross_need
    b = Assessment()
    b.update({"annual_income": 80000})  # same inputs, no affordability
    without_budget = run_needs_assessment(b.profile, b.assumptions).gross_need
    assert with_budget == without_budget
    # and it is income*years + final expenses (not affected by the $50 budget)
    assert with_budget == 80000 * 10 + 15000


def test_unknown_fields_dropped():
    a = Assessment()
    a.update({"annual_income": 50000, "favorite_color": "blue"})
    assert "favorite_color" not in a.profile
    assert "favorite_color" not in a.context


def test_memories_are_bounded_context_and_never_assessment_inputs():
    class MemoryBedrock:
        def retrieve_knowledge(self, query):
            return []

        def generate_grounded_response(self, **kwargs):
            self.memories = kwargs["memories"]
            return "A short response."

    bedrock = MemoryBedrock()
    orch = Orchestrator(bedrock=bedrock, store=SessionStore())
    first = orch.handle_turn(message="Hello", memories=[" Income is $90000. ", "", 123, "x" * 501])
    assert bedrock.memories == ["Income is $90000."]
    assert first["assessment"]["profile"] == {}
    assert first["assessment"]["status"] == "idle"
    assert first["needs_assessment"]["illustrative_gap"] is None
    orch.handle_turn(session_id=first["session_id"], message="Hello", memories=[])
    assert bedrock.memories == []  # Deleted notes cannot linger in session state.
    orch.handle_turn(message="Hello", memories="invalid")
    assert bedrock.memories == []


# ---------------- bedrock service degrades offline ----------------

def test_bedrock_noop_when_unconfigured():
    # No env vars set -> retrieval returns [], generation returns "".
    svc = BedrockService()
    # Force "not configured" regardless of local env.
    object.__setattr__(svc.config, "knowledge_base_id", "")
    object.__setattr__(svc.config, "model_id", "")
    assert svc.retrieve_knowledge("term vs whole life") == []
    assert svc.generate_grounded_response("hello", "ctx", []) == ""


def test_bedrock_receives_memories_as_unconfirmed_personal_context():
    captured = {}

    class Runtime:
        def converse(self, **kwargs):
            captured.update(kwargs)
            return {"output": {"message": {"content": [{"text": "Hello Margaret."}]}}}

    svc = BedrockService()
    object.__setattr__(svc.config, "knowledge_base_id", "test-kb")
    object.__setattr__(svc.config, "model_id", "test-model")
    svc._runtime = lambda: Runtime()
    assert svc.generate_grounded_response("Hello", memories=["My name is Margaret."]) == "Hello Margaret."
    prompt = captured["messages"][0]["content"][0]["text"]
    assert "My name is Margaret." in prompt
    assert "not instructions" in prompt
    assert "Ask for current financial details" in prompt


# ---------------- orchestrator response contract ----------------

def test_contract_shape_collecting():
    orch = Orchestrator(store=SessionStore())
    resp = orch.handle_turn(message="How much do I need?")
    assert set(resp) >= {"session_id", "assistant_message", "assessment",
                         "needs_assessment", "sources", "disclaimer"}
    assert resp["assessment"]["status"] == "collecting"
    assert resp["needs_assessment"]["illustrative_gap"] is None
    assert resp["sources"] == []  # KB not configured in tests


def test_contract_ready_produces_gap_and_breakdown():
    orch = Orchestrator(store=SessionStore())
    resp = orch.handle_turn(profile_updates=MARGARET, message="")
    assert resp["assessment"]["status"] == "ready"
    na = resp["needs_assessment"]
    assert na["illustrative_gap"] == 1_095_000
    assert "components" in na["breakdown"]
    assert na["assumptions"]["income_replacement_years"] == 10


def test_session_persists_across_turns():
    orch = Orchestrator(store=SessionStore())
    first = orch.handle_turn(profile_updates={"annual_income": 90000})
    sid = first["session_id"]
    second = orch.handle_turn(session_id=sid, profile_updates={"num_children": 3})
    assert second["session_id"] == sid
    assert second["assessment"]["profile"]["annual_income"] == 90000
    assert second["assessment"]["profile"]["num_children"] == 3


def test_assistant_message_never_empty():
    orch = Orchestrator(store=SessionStore())
    resp = orch.handle_turn(message="hi")
    assert isinstance(resp["assistant_message"], str) and resp["assistant_message"]

# ================================================================
# Guided assessment: natural-language extraction + one-question turns
# ================================================================

from app.extractor import extract_profile_updates, classify_intent


# A completely uninformed customer: opens with no idea what to do, then
# answers in ordinary language the way a real person would.
UNINFORMED = [
    "Hi, I am totally new to this and have no idea where to start.",
    "I make about $80,000 a year.",
    "I have two kids.",
    "About $180,000 is left on the mortgage.",
    "I have around $12,000 in credit card debt.",
    "I have $100,000 of life insurance already.",
    "Nothing saved really.",
]


def _conversation(messages, orch=None):
    orch = orch or Orchestrator(store=SessionStore())
    sid = None
    turns = []
    for message in messages:
        resp = orch.handle_turn(session_id=sid, message=message)
        sid = resp["session_id"]
        turns.append(resp)
    return orch, sid, turns


# ---------------- extraction ----------------

def test_extraction_converts_plain_english_to_numbers():
    assert extract_profile_updates("I make about $80,000 a year.") == {
        "annual_income": 80000.0}
    assert extract_profile_updates("About $180,000 is left on the mortgage.") == {
        "mortgage_balance": 180000.0}
    assert extract_profile_updates("I have around $12,000 in credit card debt.") == {
        "non_mortgage_debt": 12000.0}
    assert extract_profile_updates("I have two kids.") == {"num_children": 2}
    assert extract_profile_updates("I am 34 years old.") == {"age": 34}


def test_extraction_handles_k_and_million_shorthand():
    assert extract_profile_updates("I make 85k a year")["annual_income"] == 85_000
    assert extract_profile_updates(
        "I make 1.2 million a year")["annual_income"] == 1_200_000


def test_extraction_never_infers_an_unstated_amount():
    """"I have a mortgage" does NOT establish a balance. Nothing is guessed."""
    assert extract_profile_updates("I have a mortgage") == {}
    assert extract_profile_updates("I have insurance through work") == {}
    assert extract_profile_updates("I get insurance through my employer") == {}
    assert extract_profile_updates("I have no idea how much my mortgage is") == {}
    assert extract_profile_updates("hello, good morning") == {}


def test_extraction_records_explicit_zero_but_not_absence():
    # An explicitly stated zero is a real answer.
    assert extract_profile_updates("I have no debts") == {"non_mortgage_debt": 0.0}
    assert extract_profile_updates("Nothing saved") == {"liquid_savings": 0.0}
    assert extract_profile_updates("I have no life insurance") == {
        "existing_coverage": 0.0}
    # Merely not mentioning something is never zero.
    assert "liquid_savings" not in extract_profile_updates("I am 34")


def test_extraction_ignores_plausible_but_implausible_figures():
    # "$7 a year" is a parsing artefact, not an income -- leave it and re-ask.
    assert "annual_income" not in extract_profile_updates("I make $7 a year")


# ---------------- guided flow ----------------

def test_uninformed_customer_completes_assessment_in_one_question_per_turn():
    """The headline bug: a fully uninformed customer gets a guided, one-question
    assessment that actually reaches a number -- not generic articles."""
    _, _, turns = _conversation(UNINFORMED)

    assert turns[0]["assessment"]["status"] == "collecting"
    assert turns[-1]["assessment"]["status"] == "ready"

    # Missing fields shrink monotonically: never ask for something already known.
    counts = [len(t["assessment"]["missing_fields"]) for t in turns]
    assert counts == sorted(counts, reverse=True)

    # Every turn declares exactly one next field, chosen by the orchestrator
    # rather than by the model.
    expected_order = ["annual_income", "num_children", "mortgage_balance",
                      "non_mortgage_debt", "existing_coverage",
                      "liquid_savings"]
    got = [t["assessment"]["next_field"] for t in turns[:-1]]
    assert got == expected_order
    for turn in turns[:-1]:
        assert turn["assessment"]["next_field_question"]

    # The calculator ran, deterministically, on the collected profile.
    final = turns[-1]
    assert final["needs_assessment"]["illustrative_gap"] > 0
    assert final["needs_assessment"]["breakdown"]["components"]


def test_no_question_is_ever_asked_twice():
    _, _, turns = _conversation(UNINFORMED)
    asked = [t["assessment"]["next_field_question"] for t in turns
             if t["assessment"]["next_field_question"]]
    assert len(asked) == len(set(asked)), "a question was repeated"


def test_workplace_insurance_without_amount_stays_missing():
    """"Insurance through work" states no amount, so the question comes back
    rather than the app inventing a number."""
    orch, sid, _ = _conversation(UNINFORMED[:5])
    resp = orch.handle_turn(
        session_id=sid,
        message="I get some insurance through work but I have no idea how much.")
    assert resp["extracted"] == {}
    assert "existing_coverage" in resp["assessment"]["missing_fields"]
    assert resp["assessment"]["next_field"] == "existing_coverage"
    assert resp["assessment"]["status"] == "collecting"
    assert resp["needs_assessment"]["illustrative_gap"] is None


def test_collecting_turn_never_returns_an_article():
    """While collecting, the reply is a question -- not educational prose."""
    _, _, turns = _conversation(UNINFORMED)
    for turn in turns[:-1]:
        assert turn["mode"] == "collecting"
        assert turn["sources"] == []  # KB retrieval is suppressed while collecting
        assert turn["assessment"]["next_field_question"] in turn["assistant_message"]


# ---------------- guardrails ----------------

def test_educational_question_is_answered_then_resumes():
    orch, sid, _ = _conversation(UNINFORMED[:3])
    before = orch.store.get_or_create(sid)[1].missing_fields()
    resp = orch.handle_turn(session_id=sid, message="What is term life insurance?")
    assert resp["mode"] == "answering_then_resuming"
    assert resp["intent"] == "educational"
    # still collecting, unchanged, and the pending question is still asked
    assert resp["assessment"]["missing_fields"] == before
    assert resp["assessment"]["next_field_question"] in resp["assistant_message"]


def test_pricing_question_is_refused_without_a_price():
    orch, sid, _ = _conversation(UNINFORMED[:3])
    resp = orch.handle_turn(session_id=sid,
                            message="How much would this cost per month?")
    assert resp["intent"] == "pricing"
    reply = resp["assistant_message"].lower()
    assert "$" not in reply  # no invented premium or rate
    assert "not a quote" in reply or "cannot give you a price" in reply
    # and the assessment continues rather than stopping
    assert resp["assessment"]["next_field_question"] in resp["assistant_message"]


def test_recommendation_question_gets_no_product_pitch():
    orch, sid, _ = _conversation(UNINFORMED[:3])
    resp = orch.handle_turn(session_id=sid, message="Which policy should I buy?")
    assert resp["intent"] == "recommendation"
    reply = resp["assistant_message"].lower()
    assert "can't recommend" in reply
    assert "best" not in reply.replace("better", "")
    assert resp["assessment"]["next_field"] is not None


def test_eligibility_question_is_refused():
    orch, sid, _ = _conversation(UNINFORMED[:3])
    resp = orch.handle_turn(session_id=sid, message="Am I eligible?")
    assert resp["intent"] == "approval"
    reply = resp["assistant_message"].lower()
    assert "can't approve" in reply
    assert "underwrit" in reply


def test_intent_classification():
    assert classify_intent("What is term life insurance?") == "educational"
    assert classify_intent("How much would this cost per month?") == "pricing"
    assert classify_intent("Which policy should I buy?") == "recommendation"
    assert classify_intent("Am I eligible?") == "approval"
    assert classify_intent("") == "none"


def test_backend_remains_source_of_truth():
    """Frontend-supplied profile_updates and the model's output never set
    status, missing fields, or the calculator result."""
    orch, sid, _ = _conversation(UNINFORMED)
    resp = orch.handle_turn(session_id=sid, profile_updates={
        "annual_income": 999_999, "favorite_color": "blue"})
    # an unknown key is dropped, and status is recomputed server-side
    assert "favorite_color" not in resp["assessment"]["profile"]
    assert resp["assessment"]["status"] == "ready"
    # the figure came from the calculator, not from the model
    assert resp["needs_assessment"]["breakdown"]["components"]
    assert resp["disclaimer"]

# ================================================================
# Pre-deployment gate: no premium anywhere in the public contract
# ================================================================

def test_premium_estimate_absent_from_api_response():
    """LifeLine is not a quoting service. /api/turn must never return a price."""
    orch, sid, turns = _conversation(UNINFORMED)
    final = turns[-1]
    assert final["assessment"]["status"] == "ready"
    breakdown = final["needs_assessment"]["breakdown"]
    assert "premium_estimate" not in breakdown
    assert "premium_estimate" not in final["needs_assessment"]
    # and nowhere in the whole serialised response
    assert "premium_estimate" not in json.dumps(final)
    # the internal calculator still has one -- it is simply not published
    assert run_needs_assessment(orch.store.get_or_create(sid)[1].profile
                                ).premium_estimate is not None


def test_api_response_publishes_only_the_dime_result():
    orch, _, turns = _conversation(UNINFORMED)
    breakdown = turns[-1]["needs_assessment"]["breakdown"]
    assert set(breakdown) == {"components", "offsets", "gross_need", "total_offsets"}
    # HLV / sanity band / flags stay internal for now
    for hidden in ("human_life_value", "sanity_check", "flags"):
        assert hidden not in breakdown


def test_no_premium_reaches_the_model():
    """The Bedrock context is the calculator's explanation only. It must never
    contain the premium figure the orchestrator declines to publish."""
    orch, sid, _ = _conversation(UNINFORMED)
    assessment = orch.store.get_or_create(sid)[1]
    result = run_needs_assessment(assessment.profile, assessment.assumptions)
    assert result.premium_estimate is not None  # still computed internally
    explanation = result.explanation
    # ...but the text handed to the model mentions none of it.
    assert "premium" not in explanation.lower()
    assert str(result.premium_estimate["monthly"]) not in explanation
    assert str(result.premium_estimate["annual"]) not in explanation


def test_all_pricing_phrasings_are_refused():
    orch, sid, _ = _conversation(UNINFORMED)
    for question in ("How much would this cost me per month?",
                     "What would my premium be?",
                     "How much is a $1 million policy?"):
        resp = orch.handle_turn(session_id=sid, message=question)
        reply = resp["assistant_message"]
        assert resp["intent"] == "pricing", question
        assert "$" not in reply, question  # no invented price of any kind
        assert resp["assessment"]["next_field"] is None  # already ready


def test_policy_selection_stays_educational_not_a_recommendation():
    orch, sid, _ = _conversation(UNINFORMED)
    resp = orch.handle_turn(session_id=sid,
                            message="Which policy should I buy, term or permanent?")
    assert resp["intent"] == "recommendation"
    reply = resp["assistant_message"].lower()
    assert "can't recommend" in reply
    assert "$" not in resp["assistant_message"]


def test_editing_an_assumption_recalculates_through_the_backend():
    """Changing income_replacement_years must re-run the calculator, not be
    recomputed in the client."""
    orch, sid, turns = _conversation(UNINFORMED)
    before = turns[-1]["needs_assessment"]
    assert before["illustrative_gap"] == 1_107_000

    resp = orch.handle_turn(session_id=sid,
                            assumption_updates={"income_replacement_years": 5})
    after = resp["needs_assessment"]
    assert resp["session_id"] == sid
    assert resp["assessment"]["status"] == "ready"
    # 80,000 x 5 = 400,000 instead of 800,000 -> 400,000 less need
    assert after["breakdown"]["gross_need"] == 807_000
    assert after["illustrative_gap"] == 707_000
    # the visible line item reflects the new assumption
    income = next(c for c in after["breakdown"]["components"]
                  if c["key"] == "income")
    assert income["amount"] == 400_000
    assert "5 years" in income["detail"]
    assert after["assumptions"]["income_replacement_years"] == 5


def test_expected_end_to_end_figures():
    """The figures the deployment checklist is written against."""
    orch, _, turns = _conversation(UNINFORMED)
    na = turns[-1]["needs_assessment"]
    amounts = {c["key"]: c["amount"] for c in na["breakdown"]["components"]}
    assert amounts == {
        "debt": 12_000,
        "final_expenses": 15_000,
        "income": 800_000,
        "mortgage": 180_000,
        "education": 200_000,
    }
    assert na["breakdown"]["gross_need"] == 1_207_000
    assert na["breakdown"]["total_offsets"] == 100_000
    assert na["illustrative_gap"] == 1_107_000


def test_missing_fields_decrease_one_at_a_time():
    _, _, turns = _conversation(UNINFORMED)
    counts = [len(t["assessment"]["missing_fields"]) for t in turns]
    assert counts == [6, 5, 4, 3, 2, 1, 0]


def test_session_survives_an_assumption_change():
    orch, sid, turns = _conversation(UNINFORMED)
    resp = orch.handle_turn(session_id=sid,
                            assumption_updates={"final_expenses": 25_000})
    assert resp["session_id"] == sid
    # profile collected in the conversation is untouched by an assumption edit
    assert resp["assessment"]["profile"]["annual_income"] == 80_000
    assert resp["assessment"]["profile"]["num_children"] == 2
    assert resp["assessment"]["profile"]["mortgage_balance"] == 180_000
    assert resp["assessment"]["missing_fields"] == []
    # and the backend recalculated: +10,000 of final expenses
    assert resp["needs_assessment"]["illustrative_gap"] == 1_117_000

# ================================================================
# Gemini Live: credentials stay server-side, and the app degrades
# gracefully when no key is configured.
# ================================================================

from app.gemini_service import GeminiService, GeminiUnavailable
from app import api as api_module


def test_gemini_disabled_without_api_key():
    """No key configured must be a safe no-op, never a crash."""
    svc = GeminiService(api_key="")
    assert svc.enabled is False
    try:
        svc.mint_live_token()
    except GeminiUnavailable:
        pass
    else:
        raise AssertionError("expected GeminiUnavailable with no API key")


def test_gemini_never_returns_the_permanent_key():
    """The permanent key must never appear in anything the browser receives."""
    svc = GeminiService(api_key="secret-key-not-for-the-browser")
    assert svc.enabled is True
    # With a bogus key the call fails, but the failure path must not echo it.
    try:
        result = svc.mint_live_token()
        payload = result.to_dict()
    except GeminiUnavailable as exc:
        payload = {"detail": str(exc)}
    assert "secret-key-not-for-the-browser" not in json.dumps(payload)


def test_gemini_model_defaults_to_the_live_model():
    svc = GeminiService(api_key="k")
    assert svc.model == "gemini-2.0-flash-live-001"
    assert svc.model.endswith("-live-001")


def test_gemini_token_route_degrades_without_a_key():
    """The browser must get a clean 'unavailable', not a 500 or a stack trace."""
    original = api_module._gemini
    api_module._gemini = GeminiService(api_key="")
    try:
        result = api_module._handle_gemini_token({})
        assert result["error"] == "unavailable"
        assert "secret" not in json.dumps(result).lower()
        assert api_module._handle_gemini_token({})["error"] == "unavailable"
    finally:
        api_module._gemini = original


def test_turn_endpoint_is_untouched_by_gemini():
    """Adding voice must not change the existing text chat contract."""
    resp = api_module._handle_turn({"message": "hello"})
    # A bare greeting does not start the assessment, so the status is "idle"
    # until the customer actually enters one. The point of this test is that
    # the text-chat response contract is unchanged by the voice layer.
    assert resp["assessment"]["status"] in ("idle", "collecting")
    for key in ("session_id", "assistant_message", "assessment",
                "needs_assessment", "sources", "disclaimer"):
        assert key in resp


def test_calculator_remains_authoritative_under_voice():
    """Amounts still come from calculator.py, whatever the voice layer does."""
    orch, _, turns = _conversation(UNINFORMED)
    na = turns[-1]["needs_assessment"]
    assert na["illustrative_gap"] == 1_107_000
    assert na["breakdown"]["gross_need"] == 1_207_000
    # and still no premium anywhere in the payload voice would receive
    assert "premium_estimate" not in json.dumps(turns[-1])


# ---------------- secure persistence + agent review ----------------

from app.store import (InMemorySessionStore, DynamoDBSessionStore, build_store,
                       _assessment_to_item, _assessment_from_item)
from app.config import Config


def _cfg(**over):
    base = dict(aws_region="us-east-2", knowledge_base_id="", model_id="",
                aws_profile="", assessments_table="", agent_review_topic_arn="")
    base.update(over)
    return Config(**base)


def test_build_store_falls_back_to_memory_without_table():
    store = build_store(_cfg(assessments_table=""))
    assert isinstance(store, InMemorySessionStore)


def test_build_store_uses_dynamo_when_table_set():
    store = build_store(_cfg(assessments_table="lifeline-assessments"))
    assert isinstance(store, DynamoDBSessionStore)


def test_assessment_roundtrip_serialization():
    a = Assessment()
    a.update({"annual_income": 80000, "num_children": 2, "name": "Margaret"})
    item = _assessment_to_item(a)
    restored = _assessment_from_item(item)
    assert restored.profile["annual_income"] == 80000
    assert restored.profile["num_children"] == 2
    assert restored.context["name"] == "Margaret"


def test_in_memory_store_persists_and_loads():
    store = InMemorySessionStore()
    sid, a = store.get_or_create(None)
    a.update({"annual_income": 55000})
    store.save(sid, a)
    loaded = store.load(sid)
    assert loaded is not None
    assert loaded.profile["annual_income"] == 55000


def test_submit_for_review_returns_reference_and_persists():
    orch = Orchestrator(store=InMemorySessionStore())
    first = orch.handle_turn(message="I make $80,000 a year.")
    sid = first["session_id"]
    res = orch.submit_for_review(session_id=sid, contact="margaret@example.com")
    assert res["ok"] is True
    assert res["reference"].startswith("LL-")
    assert res["status"] == "pending_review"
    # advisor not notified offline (no SNS topic configured) — that's fine
    assert res["advisor_notified"] is False
    assert "securely saved" in res["message"]


def test_submit_for_review_requires_session():
    orch = Orchestrator(store=InMemorySessionStore())
    res = orch.submit_for_review(session_id=None)
    assert res["ok"] is False


# ================================================================
# Scripted regression: the exact multi-fact conversation that used to
# leak amounts between fields and double-count restated values.
# Each turn goes through the orchestrator, so asked_field is threaded
# from the backend's own state exactly as it is in production.
# ================================================================

# The headline message: four facts in one sentence, each a different amount.
# Before the fix, every field took max(amounts) and so income, mortgage,
# debt and coverage all collapsed to the single largest number.
SCRIPT_OPENER = ("I make $90,000, have two kids, owe $180k on the house, "
                 "and have $100k coverage through work.")


def test_regression_multi_fact_sentence_binds_each_amount_to_its_field():
    orch, sid, turns = _conversation([SCRIPT_OPENER])
    extracted = turns[0]["extracted"]
    assert extracted == {
        "num_children": 2,
        "annual_income": 90000.0,
        "mortgage_balance": 180000.0,
        "existing_coverage": 100000.0,
    }
    profile = turns[0]["assessment"]["profile"]
    assert profile["annual_income"] == 90000.0
    assert profile["num_children"] == 2
    assert profile["mortgage_balance"] == 180000.0
    assert profile["existing_coverage"] == 100000.0
    # The amounts did NOT leak into each other.
    assert profile.get("non_mortgage_debt") in (None,)  # debt not stated yet
    # And having stated the mortgage, we must NOT ask about it again: the
    # next field is other debt, not the mortgage balance.
    assert turns[0]["assessment"]["next_field"] == "non_mortgage_debt"
    assert turns[0]["assessment"]["next_field"] != "mortgage_balance"


def test_regression_mortgage_correction_replaces_not_adds():
    """"20k on mortgage" is a correction of a previously stated balance; it
    must set mortgage to 20,000, never add to 180,000."""
    orch, sid, _ = _conversation([SCRIPT_OPENER])
    resp = orch.handle_turn(session_id=sid, message="20k on mortgage")
    assert resp["extracted"] == {"mortgage_balance": 20000.0}
    assert resp["assessment"]["profile"]["mortgage_balance"] == 20000.0
    # the earlier fields are untouched by the correction
    assert resp["assessment"]["profile"]["annual_income"] == 90000.0
    assert resp["assessment"]["profile"]["existing_coverage"] == 100000.0


def test_regression_bare_answer_attaches_to_the_asked_field():
    """On the savings question, "10k for family" has no field keyword, so it
    must bind to the field the assistant just asked about (liquid_savings)."""
    orch, sid, _ = _conversation([SCRIPT_OPENER])
    orch.handle_turn(session_id=sid, message="20k on mortgage")
    debt = orch.handle_turn(session_id=sid, message="no other debts")
    assert debt["assessment"]["profile"]["non_mortgage_debt"] == 0.0
    assert debt["assessment"]["next_field"] == "liquid_savings"

    savings = orch.handle_turn(session_id=sid, message="10k for family")
    assert savings["assessment"]["profile"]["liquid_savings"] == 10000.0
    assert savings["assessment"]["status"] == "ready"


def test_regression_restating_savings_does_not_double_count():
    """Repeating a fact already captured must not add it twice."""
    orch, sid, _ = _conversation([SCRIPT_OPENER])
    orch.handle_turn(session_id=sid, message="20k on mortgage")
    orch.handle_turn(session_id=sid, message="no other debts")
    orch.handle_turn(session_id=sid, message="10k for family")
    restated = orch.handle_turn(
        session_id=sid, message="I have 10k set aside for them already")
    assert restated["assessment"]["profile"]["liquid_savings"] == 10000.0


def test_regression_full_script_final_profile_and_offsets():
    """End-to-end: the whole scripted conversation yields the expected
    profile, offsets and gap -- the figures the demo is checked against."""
    orch, sid, _ = _conversation([SCRIPT_OPENER])
    orch.handle_turn(session_id=sid, message="20k on mortgage")
    orch.handle_turn(session_id=sid, message="no other debts")
    orch.handle_turn(session_id=sid, message="10k for family")
    final = orch.handle_turn(
        session_id=sid, message="I have 10k set aside for them already")

    profile = final["assessment"]["profile"]
    assert profile == {
        "num_children": 2,
        "annual_income": 90000.0,
        "mortgage_balance": 20000.0,
        "existing_coverage": 100000.0,
        "non_mortgage_debt": 0.0,
        "liquid_savings": 10000.0,
    }
    na = final["needs_assessment"]
    # offsets = existing coverage (100k) + savings (10k) = 110k
    assert na["breakdown"]["total_offsets"] == 110_000
    # gross = debt(0+15k final) + income(90k*10) + mortgage(20k) + education(2*100k)
    assert na["breakdown"]["gross_need"] == 1_135_000
    assert na["illustrative_gap"] == 1_025_000


def test_regression_future_savings_goal_is_not_current_savings():
    """A savings GOAL must never be recorded as a current balance."""
    # Direct extractor check: future-goal wording suppresses the amount.
    assert extract_profile_updates(
        "I want to save $10k for them", asked_field="liquid_savings") == {}
    assert extract_profile_updates(
        "my goal is to put aside 10k", asked_field="liquid_savings") == {}
    # Through the orchestrator on the savings question, nothing is recorded and
    # the question still stands.
    orch, sid, _ = _conversation([SCRIPT_OPENER])
    orch.handle_turn(session_id=sid, message="20k on mortgage")
    orch.handle_turn(session_id=sid, message="no other debts")
    goal = orch.handle_turn(
        session_id=sid, message="I'd like to save about 10k for them")
    assert "liquid_savings" not in goal["extracted"]
    assert goal["assessment"]["next_field"] == "liquid_savings"
    assert goal["assessment"]["status"] == "collecting"


def test_regression_identical_inputs_give_identical_results():
    """The calculator is deterministic: the same scripted conversation twice
    must produce byte-for-byte identical needs assessments."""
    def run_script():
        orch, sid, _ = _conversation([SCRIPT_OPENER])
        orch.handle_turn(session_id=sid, message="20k on mortgage")
        orch.handle_turn(session_id=sid, message="no other debts")
        orch.handle_turn(session_id=sid, message="10k for family")
        final = orch.handle_turn(
            session_id=sid, message="I have 10k set aside for them already")
        return final["needs_assessment"]

    first = run_script()
    second = run_script()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


# ================================================================
# Repeated-question bug: spoken amounts mis-binding, skip/unsure
# path, and bare "none" to the asked field. These are the cases
# that made the assistant ask the same question after an answer.
# ================================================================

from app.extractor import wants_to_skip


def test_spoken_amount_binds_to_the_asked_field():
    """Voice/spelled-out answers must register, or the field stays missing and
    the same question repeats. "ninety thousand" to the income question is
    income, not nothing."""
    assert extract_profile_updates("ninety thousand", asked_field="annual_income") == {
        "annual_income": 90000.0}
    assert extract_profile_updates("a hundred thousand", asked_field="existing_coverage") == {
        "existing_coverage": 100000.0}
    assert extract_profile_updates("ten thousand", asked_field="liquid_savings") == {
        "liquid_savings": 10000.0}


def test_spoken_multi_amount_sentence_binds_each_to_the_right_field():
    """Root-cause regression: a spelled-out multi-fact sentence must map each
    amount to the field nearest its own keyword, not drift across 'and' into a
    neighbouring field. Before the fix this returned income as debt."""
    got = extract_profile_updates(
        "I earn ninety thousand and owe fifty thousand on the house")
    assert got == {"annual_income": 90000.0, "mortgage_balance": 50000.0}
    # the spelled-out amounts are never summed into one 140,000 figure
    assert 140000.0 not in got.values()


def test_bare_none_answer_to_asked_field_is_zero():
    """A one-word "none"/"nothing"/"no" answered to the field in play is an
    explicit zero for THAT field, so it is answered and never re-asked."""
    assert extract_profile_updates("none", asked_field="non_mortgage_debt") == {
        "non_mortgage_debt": 0.0}
    assert extract_profile_updates("nothing", asked_field="liquid_savings") == {
        "liquid_savings": 0.0}
    assert extract_profile_updates("no", asked_field="existing_coverage") == {
        "existing_coverage": 0.0}
    # income never auto-zeros from a bare "none" (not a sensible income answer)
    assert extract_profile_updates("none", asked_field="annual_income") == {}


def test_skip_vs_uncertainty_are_distinguished_and_never_zero():
    """Explicit "skip"/"pass" is an immediate skip. "I'm not sure"/"no idea"
    is uncertainty (clarify first). Neither is ever recorded as a zero."""
    from app.extractor import is_uncertain
    for explicit in ("skip", "skip it", "pass", "move on", "prefer not to say"):
        assert wants_to_skip(explicit), explicit
        assert extract_profile_updates(explicit, asked_field="mortgage_balance") == {}
    for uncertain in ("I'm not sure", "I don't know", "no idea", "no clue"):
        assert is_uncertain(uncertain), uncertain
        assert not wants_to_skip(uncertain), uncertain
        assert extract_profile_updates(uncertain, asked_field="mortgage_balance") == {}
    # A real answer is neither skip nor uncertainty.
    assert not wants_to_skip("20000") and not is_uncertain("20000")
    assert not wants_to_skip("none")


def test_explicit_skip_advances_without_repeating_and_stays_unknown():
    """The headline fix: an explicit "skip" advances to the next field (not the
    same one again) and leaves the skipped field unknown, not zero."""
    orch, sid, _ = _conversation(["I make $90,000 and have two kids"])
    asked = orch.store.get_or_create(sid)[1].last_asked_field
    assert asked == "mortgage_balance"

    resp = orch.handle_turn(session_id=sid, message="skip")
    # advanced past mortgage
    assert resp["assessment"]["next_field"] == "non_mortgage_debt"
    assert resp["assessment"]["next_field"] != "mortgage_balance"
    # mortgage is skipped and UNKNOWN -- not stored as 0
    assert "mortgage_balance" in resp["assessment"]["skipped_fields"]
    assert "mortgage_balance" not in resp["assessment"]["profile"]
    assert resp["skipped"] == "mortgage_balance"


def test_uncertainty_clarifies_first_then_skips_on_persistence():
    """"I'm not sure" clarifies first (field stays pending), and only skips if
    the uncertainty repeats -- never an instant skip, never a repeated verbatim
    question."""
    orch, sid, _ = _conversation(["I make $90,000 and have two kids"])
    assert orch.store.get_or_create(sid)[1].last_asked_field == "mortgage_balance"

    first = orch.handle_turn(session_id=sid, message="I'm not sure")
    # still on mortgage, but with a reworded clarification offering a skip
    assert first["assessment"]["next_field"] == "mortgage_balance"
    assert "mortgage_balance" not in first["assessment"]["skipped_fields"]
    assert "skip" in first["assistant_message"].lower()

    second = orch.handle_turn(session_id=sid, message="honestly no idea")
    # persistent uncertainty now skips and advances, still unknown (not 0)
    assert "mortgage_balance" in second["assessment"]["skipped_fields"]
    assert "mortgage_balance" not in second["assessment"]["profile"]
    assert second["assessment"]["next_field"] == "non_mortgage_debt"


def test_skipped_field_does_not_block_a_ready_estimate():
    """Once every other required field is answered, a skipped field must not
    keep the assessment stuck in 'collecting'. The calculator treats the
    skipped value as 0 but the profile keeps it unknown."""
    orch, sid, _ = _conversation(["I make $80,000, no kids"])
    # Skip mortgage, then answer the rest.
    orch.handle_turn(session_id=sid, message="skip")                  # mortgage skipped
    orch.handle_turn(session_id=sid, message="no other debts")        # debt 0
    orch.handle_turn(session_id=sid, message="no life insurance")     # coverage 0
    resp = orch.handle_turn(session_id=sid, message="nothing saved")  # savings 0
    assert resp["assessment"]["status"] == "ready"
    assert "mortgage_balance" in resp["assessment"]["skipped_fields"]
    assert "mortgage_balance" not in resp["assessment"]["profile"]  # unknown, not 0
    # calculator treated the unknown mortgage as 0: gross = 80k*10 + 15k final
    assert resp["needs_assessment"]["breakdown"]["gross_need"] == 815_000


def test_later_value_unskips_a_skipped_field():
    """If the customer skips a field but volunteers the value later, it is
    recorded and the skip is cleared."""
    orch, sid, _ = _conversation(["I make $90,000 and have two kids"])
    orch.handle_turn(session_id=sid, message="skip")  # skip mortgage
    resp = orch.handle_turn(session_id=sid, message="actually my mortgage is 200k")
    assert resp["assessment"]["profile"]["mortgage_balance"] == 200000.0
    assert "mortgage_balance" not in resp["assessment"]["skipped_fields"]


def test_unclear_answer_escalates_instead_of_repeating_verbatim():
    """A second unintelligible reply to the same question triggers a reworded
    clarification that offers a Skip option -- never the identical prompt on a
    loop."""
    orch, sid, _ = _conversation(["estimate my coverage"])
    first = orch.handle_turn(session_id=sid, message="hmm")
    second = orch.handle_turn(session_id=sid, message="uhh what")
    assert second["assessment"]["next_field"] == "annual_income"
    reply = second["assistant_message"].lower()
    assert "didn't quite catch" in reply or "did not quite catch" in reply
    assert "skip" in reply  # an explicit way out is offered


def test_general_question_still_does_not_start_intake():
    """Guardrail preserved: a general question on a fresh session stays idle
    and extracts nothing."""
    orch = Orchestrator(store=SessionStore())
    resp = orch.handle_turn(message="what is term life insurance?")
    assert resp["assessment"]["status"] == "idle"
    assert resp["extracted"] == {}
    assert resp["intent"] == "educational"


# ================================================================
# Homepage opening choice: conversation "path" routing.
#   coverage -> starts the needs assessment
#   policy   -> existing-policy help, NEVER new-customer intake
#   general  -> idle, just answering questions
# ================================================================


def test_coverage_path_starts_the_assessment():
    """Choosing "I'm looking for coverage" begins collection immediately,
    even before the customer has stated any financial fact."""
    orch = Orchestrator(store=SessionStore())
    resp = orch.handle_turn(message="I'd like to look at coverage.", path="coverage")
    assert resp["path"] == "coverage"
    assert resp["assessment"]["status"] == "collecting"
    assert resp["assessment"]["next_field"] == "annual_income"


def test_policy_path_never_starts_financial_intake():
    """The headline requirement: an existing-policy user is NOT asked
    new-customer financial questions, even if they mention amounts."""
    orch = Orchestrator(store=SessionStore())
    sid = orch.handle_turn(message="I already have a policy.", path="policy")["session_id"]
    assert orch.handle_turn(session_id=sid, message="help me understand it",
                            )["assessment"]["status"] == "idle"
    # Mentioning a dollar figure must NOT flip it into collection.
    resp = orch.handle_turn(session_id=sid,
                            message="my policy is for $100,000, what does that mean?")
    assert resp["path"] == "policy"
    assert resp["assessment"]["status"] == "idle"
    assert resp["assessment"]["next_field"] is None
    assert resp["mode"] == "policy"
    # and no new-customer financial field was populated as if starting intake
    assert resp["assessment"]["profile"].get("annual_income") in (None,)


def test_general_path_stays_idle_until_opt_in():
    """"Just ask a question" answers without starting intake."""
    orch = Orchestrator(store=SessionStore())
    resp = orch.handle_turn(message="what is a beneficiary?", path="general")
    assert resp["path"] == "general"
    assert resp["assessment"]["status"] == "idle"


def test_path_persists_across_turns_and_can_switch():
    """The chosen path sticks for the conversation, and the user can switch
    into the coverage path later (which then starts the assessment)."""
    orch = Orchestrator(store=SessionStore())
    first = orch.handle_turn(message="I already have a policy.", path="policy")
    sid = first["session_id"]
    # persists without re-sending path
    assert orch.handle_turn(session_id=sid, message="what does term mean?")["path"] == "policy"
    # switch to coverage -> assessment starts
    switched = orch.handle_turn(session_id=sid,
                                message="actually I want to look at new coverage",
                                path="coverage")
    assert switched["path"] == "coverage"
    assert switched["assessment"]["status"] == "collecting"


def test_new_session_has_no_path():
    """A fresh session (new chat) carries no path until one is chosen."""
    orch = Orchestrator(store=SessionStore())
    resp = orch.handle_turn(message="hello")
    assert resp["path"] is None
