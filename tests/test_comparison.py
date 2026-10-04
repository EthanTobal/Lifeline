"""Tests for follow-up product comparisons (new-customer path).

Covers the three done-criteria for the comparison feature:
  * "What are my other options?"  -> grounded, relevant alternatives
  * "Why not this product?"       -> catalog-grounded, or honest if not modelled
  * a change in coverage goals     -> the recommendation is re-evaluated

Plus the guardrails: everything is sourced from the catalog, undocumented
fields are acknowledged rather than invented, products outside the catalog are
not fabricated, only the most relevant alternatives surface initially, and the
comparison never fires on the existing-policy path.
"""

from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO_ROOT, "backend")
for p in (REPO_ROOT, BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

from app import product_matcher as pm  # noqa: E402
from app.orchestrator import Orchestrator  # noqa: E402
from app import extractor  # noqa: E402

CATALOG_IDS = {
    "lifeline-term-20", "lifeline-term-30",
    "lifeline-indexed-protection", "lifeline-variable-protection",
}

GAP = {"illustrative_gap": 1_155_000}
BASE_PROFILE = {
    "annual_income": 80000, "num_children": 2, "mortgage_balance": 200000,
    "non_mortgage_debt": 20000, "existing_coverage": 50000, "liquid_savings": 30000,
}
# A customer leaning temporary / term (so the current match is term-20).
TERM_CTX = {"coverage_duration_pref": "temporary", "cash_value_interest": "no"}
TERM_PROFILE = {**BASE_PROFILE, "term_years": 20}


# ---------------------------------------------------------------------------
# compare_products: structure, grounding, relevance
# ---------------------------------------------------------------------------
class TestCompareProducts(unittest.TestCase):

    def test_current_suggestion_leads_and_alternatives_exclude_it(self):
        c = pm.compare_products(TERM_PROFILE, GAP, TERM_CTX)
        self.assertEqual(c["current_suggestion_id"], "lifeline-term-20")
        self.assertIsNotNone(c["current_suggestion"])
        self.assertTrue(c["current_suggestion"]["is_current_suggestion"])
        for alt in c["alternatives"]:
            self.assertNotEqual(alt["product_id"], "lifeline-term-20")
            self.assertFalse(alt["is_current_suggestion"])

    def test_only_most_relevant_shown_initially(self):
        c = pm.compare_products(TERM_PROFILE, GAP, TERM_CTX)
        # Three alternatives exist; only the initial cut is shown, with a flag
        # that more remain.
        self.assertEqual(c["alternatives_total"], 3)
        self.assertEqual(len(c["alternatives"]), pm._INITIAL_ALTERNATIVES)
        self.assertTrue(c["has_more"])

    def test_include_all_returns_every_alternative(self):
        c = pm.compare_products(TERM_PROFILE, GAP, TERM_CTX, include_all=True)
        self.assertEqual(len(c["alternatives"]), 3)
        self.assertFalse(c["has_more"])

    def test_alternatives_sorted_closest_first(self):
        c = pm.compare_products(TERM_PROFILE, GAP, TERM_CTX, include_all=True)
        distances = [a["distance"] for a in c["alternatives"]]
        self.assertEqual(distances, sorted(distances))
        # Term-30 (same term family) must be the closest alternative.
        self.assertEqual(c["alternatives"][0]["product_id"], "lifeline-term-30")

    def test_every_entry_is_catalog_sourced(self):
        c = pm.compare_products(TERM_PROFILE, GAP, TERM_CTX, include_all=True)
        for alt in c["alternatives"]:
            self.assertIn(alt["product_id"], CATALOG_IDS)
            # What it does + benefits come straight from the catalog entry.
            policy = pm._policy(alt["product_id"])
            self.assertEqual(alt["what_it_does"], policy["plain_language"])
            self.assertTrue(set(alt["benefits"]).issubset(set(policy["benefits"])))

    def test_each_alternative_has_reason_and_priority_change(self):
        c = pm.compare_products(TERM_PROFILE, GAP, TERM_CTX, include_all=True)
        for alt in c["alternatives"]:
            # Why it fits less closely must be present for every alternative.
            self.assertTrue(alt["fits_less_closely_because"])
            # A permanent alternative must explain the priority change that
            # would make it a better fit (term alternatives can be a tie).
            if alt["policy_type"] and "universal" in alt["policy_type"].lower():
                self.assertTrue(alt["would_fit_better_if"])

    def test_undocumented_fields_are_acknowledged_not_invented(self):
        c = pm.compare_products(TERM_PROFILE, GAP, TERM_CTX, include_all=True)
        for alt in c["alternatives"]:
            # The demo catalog documents no eligibility/availability, so every
            # entry must surface those as explicitly undocumented.
            self.assertIn("eligibility", alt["undocumented"])
            self.assertIn("availability", alt["undocumented"])

    def test_primary_tradeoff_is_the_catalog_limitation(self):
        c = pm.compare_products(TERM_PROFILE, GAP, TERM_CTX, include_all=True)
        for alt in c["alternatives"]:
            policy = pm._policy(alt["product_id"])
            self.assertEqual(alt["primary_tradeoff"], policy["limitations"][0])


# ---------------------------------------------------------------------------
# resolve_product_query: name a product, catalog-only
# ---------------------------------------------------------------------------
class TestResolveProductQuery(unittest.TestCase):

    def test_whole_life_is_not_fabricated(self):
        r = pm.resolve_product_query("why not whole life?")
        self.assertFalse(r["in_catalog"])
        self.assertIsNone(r["product_id"])
        self.assertEqual(r["named_term"], "whole life")
        self.assertIn("doesn't include a standalone whole-life", r["note"])

    def test_variable_resolves_to_vul(self):
        r = pm.resolve_product_query("why not the variable one?")
        self.assertTrue(r["in_catalog"])
        self.assertEqual(r["product_id"], "lifeline-variable-protection")

    def test_indexed_resolves_to_iul(self):
        r = pm.resolve_product_query("what about indexed universal life?")
        self.assertEqual(r["product_id"], "lifeline-indexed-protection")

    def test_term_resolves_to_a_term_product(self):
        r = pm.resolve_product_query("why not term?")
        self.assertEqual(r["insurance_type"], "term")
        self.assertTrue(r["in_catalog"])

    def test_direct_display_name_hit(self):
        r = pm.resolve_product_query("tell me about LifeLine Term 30")
        self.assertEqual(r["product_id"], "lifeline-term-30")

    def test_outside_catalog_returns_nothing(self):
        r = pm.resolve_product_query("what about car insurance")
        self.assertIsNone(r["product_id"])
        self.assertFalse(r["in_catalog"])
        self.assertIsNone(r["named_term"])

    def test_focus_product_pulled_into_shown_set(self):
        c = pm.compare_products(TERM_PROFILE, GAP, TERM_CTX,
                                focus_product_id="lifeline-variable-protection")
        ids = [a["product_id"] for a in c["alternatives"]]
        self.assertIn("lifeline-variable-protection", ids)
        self.assertTrue(c["focus_in_catalog"])


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------
class TestComparisonIntent(unittest.TestCase):

    def test_other_options_detected(self):
        self.assertTrue(extractor.wants_comparison("What are my other options?"))

    def test_why_not_detected(self):
        self.assertTrue(extractor.wants_comparison("Why not whole life?"))

    def test_compare_detected(self):
        self.assertTrue(extractor.wants_comparison("compare these for me"))

    def test_all_options_detected(self):
        self.assertTrue(extractor.wants_all_options("show me all my options"))
        self.assertFalse(extractor.wants_all_options("what are my other options"))

    def test_unrelated_message_is_not_comparison(self):
        self.assertFalse(extractor.wants_comparison("my income is about 80k"))


# ---------------------------------------------------------------------------
# Orchestrator routing — the three done-criteria end to end
# ---------------------------------------------------------------------------
class TestOrchestratorComparison(unittest.TestCase):

    def setUp(self):
        self.o = Orchestrator()

    def _ready_session(self, prefs_msg, prefs_upd):
        r = self.o.handle_turn(session_id=None, message="estimate my coverage",
                               path="coverage")
        sid = r["session_id"]
        self.o.handle_turn(session_id=sid, message="",
                           profile_updates={**BASE_PROFILE, "term_years": 20})
        r = self.o.handle_turn(session_id=sid, message=prefs_msg,
                               profile_updates=prefs_upd)
        return sid, r

    def test_other_options_produces_grounded_comparison(self):
        sid, r = self._ready_session(
            "temporary cover for a set period, no cash value", TERM_CTX)
        self.assertIsNone(r["comparison"])  # not yet asked
        r2 = self.o.handle_turn(session_id=sid, message="What are my other options?")
        self.assertIsNotNone(r2["comparison"])
        self.assertEqual(r2["comparison"]["current_suggestion_id"], "lifeline-term-20")
        self.assertTrue(r2["comparison"]["alternatives"])
        # The grounded prose names a real catalog alternative and never a price.
        msg = r2["assistant_message"].lower()
        self.assertIn("lifeline", msg)
        self.assertNotIn("$", r2["assistant_message"])

    def test_why_not_whole_life_is_honest(self):
        sid, r = self._ready_session(
            "temporary cover for a set period, no cash value", TERM_CTX)
        r2 = self.o.handle_turn(session_id=sid, message="Why not whole life?")
        self.assertIsNotNone(r2["comparison"])
        resolved = r2["comparison"]["resolved_query"]
        self.assertFalse(resolved["in_catalog"])
        self.assertIn("whole-life", r2["assistant_message"].lower())

    def test_why_not_named_catalog_product_pulls_it_in(self):
        sid, r = self._ready_session(
            "temporary cover for a set period, no cash value", TERM_CTX)
        r2 = self.o.handle_turn(session_id=sid, message="Why not the variable one?")
        ids = [a["product_id"] for a in r2["comparison"]["alternatives"]]
        self.assertIn("lifeline-variable-protection", ids)
        self.assertTrue(r2["comparison"]["focus_in_catalog"])

    def test_goal_change_reevaluates_recommendation(self):
        sid, r = self._ready_session(
            "temporary cover for a set period, no cash value", TERM_CTX)
        # Current recommendation is term-20.
        self.assertEqual(r["recommendation"]["product"]["product_id"], "lifeline-term-20")
        # The customer changes their goal to lifelong cover with index-linked
        # cash value. The recommendation must be re-evaluated, not defended.
        r2 = self.o.handle_turn(
            session_id=sid,
            message="actually I want lifetime cover that builds cash value linked to an index",
            profile_updates={"coverage_duration_pref": "lifetime",
                             "cash_value_interest": "yes",
                             "market_exposure_pref": "index"})
        self.assertEqual(r2["recommendation"]["product"]["product_id"],
                         "lifeline-indexed-protection")

    def test_show_all_options_returns_everything(self):
        sid, r = self._ready_session(
            "temporary cover for a set period, no cash value", TERM_CTX)
        r2 = self.o.handle_turn(session_id=sid, message="show me all my options")
        self.assertFalse(r2["comparison"]["has_more"])
        self.assertEqual(len(r2["comparison"]["alternatives"]),
                         r2["comparison"]["alternatives_total"])

    def test_policy_path_never_produces_a_comparison(self):
        r = self.o.handle_turn(session_id=None,
                               message="what are my other options?", path="policy")
        self.assertIsNone(r["comparison"])

    def test_comparison_not_offered_before_an_estimate_exists(self):
        # Fresh coverage session, no profile yet: asking to compare must not
        # fabricate a comparison because there's no estimate to compare against.
        r = self.o.handle_turn(session_id=None,
                               message="what are my other options?", path="coverage")
        self.assertIsNone(r["comparison"])


if __name__ == "__main__":
    unittest.main()
