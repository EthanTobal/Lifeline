"""Unit tests for the Lifeline backend.

Run offline — no AWS, no network. Verifies the deterministic calculator,
the assessment model, and the orchestration response contract.

    cd backend && python -m pytest ../tests -q
or from repo root:
    python -m pytest tests -q
"""
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
    assert keys == {"debt", "income", "mortgage", "education"}
    income_line = next(b for b in r.breakdown if b["key"] == "income")
    assert income_line["amount"] == 800_000


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
    a = Assessment()
    assert a.status() == "collecting"
    assert "annual_income" in a.missing_fields()
    a.update({"annual_income": 60000})
    assert a.status() == "ready"
    assert a.missing_fields() == []


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


# ---------------- bedrock service degrades offline ----------------

def test_bedrock_noop_when_unconfigured():
    # No env vars set -> retrieval returns [], generation returns "".
    svc = BedrockService()
    # Force "not configured" regardless of local env.
    object.__setattr__(svc.config, "knowledge_base_id", "")
    object.__setattr__(svc.config, "model_id", "")
    assert svc.retrieve_knowledge("term vs whole life") == []
    assert svc.generate_grounded_response("hello", "ctx", []) == ""


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
