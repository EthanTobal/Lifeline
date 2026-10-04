"""Tests for the inspectable product matcher (new-customer path).

Focus: the explicit matching rules are deterministic, tie to stated needs,
reuse known info, ask one distinguishing question at a time, and respect the
guardrails (catalog-only ids, coverage reused from the calculator, no premiums
or affordability claims, no push when no additional coverage is indicated).
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

# Catalog ids the matcher is allowed to suggest.
CATALOG_IDS = {
    "lifeline-term-20", "lifeline-term-30",
    "lifeline-indexed-protection", "lifeline-variable-protection",
}

GAP = {"illustrative_gap": 500000}
PROFILE = {"annual_income": 90000, "num_children": 2}


class TestDifferentGoalsDifferentMatches(unittest.TestCase):

    def test_short_temporary_matches_term20(self):
        r = pm.match_product({**PROFILE, "term_years": 20}, GAP,
                             {"coverage_duration_pref": "temporary"})
        self.assertEqual(r.suggested_product_id, "lifeline-term-20")
        self.assertEqual(r.rule_fired, "term20_short_temporary")
        self.assertFalse(r.provisional)

    def test_long_temporary_matches_term30(self):
        r = pm.match_product({**PROFILE, "term_years": 30}, GAP,
                             {"coverage_duration_pref": "temporary"})
        self.assertEqual(r.suggested_product_id, "lifeline-term-30")
        self.assertEqual(r.rule_fired, "term30_long_temporary")

    def test_lifetime_index_matches_iul(self):
        r = pm.match_product(PROFILE, GAP, {
            "coverage_duration_pref": "lifetime",
            "cash_value_interest": "yes",
            "market_exposure_pref": "index"})
        self.assertEqual(r.suggested_product_id, "lifeline-indexed-protection")

    def test_lifetime_investment_matches_vul(self):
        r = pm.match_product(PROFILE, GAP, {
            "coverage_duration_pref": "lifetime",
            "cash_value_interest": "yes",
            "market_exposure_pref": "investment"})
        self.assertEqual(r.suggested_product_id, "lifeline-variable-protection")

    def test_four_distinct_products_are_reachable(self):
        results = {
            pm.match_product({**PROFILE, "term_years": 20}, GAP,
                             {"coverage_duration_pref": "temporary"}).suggested_product_id,
            pm.match_product({**PROFILE, "term_years": 30}, GAP,
                             {"coverage_duration_pref": "temporary"}).suggested_product_id,
            pm.match_product(PROFILE, GAP, {"coverage_duration_pref": "lifetime",
                             "cash_value_interest": "yes",
                             "market_exposure_pref": "index"}).suggested_product_id,
            pm.match_product(PROFILE, GAP, {"coverage_duration_pref": "lifetime",
                             "cash_value_interest": "yes",
                             "market_exposure_pref": "investment"}).suggested_product_id,
        }
        self.assertEqual(len(results), 4)


class TestConsistencyAndGrounding(unittest.TestCase):

    def test_same_profile_gives_consistent_result(self):
        ctx = {"coverage_duration_pref": "lifetime", "cash_value_interest": "yes",
               "market_exposure_pref": "index"}
        a = pm.match_product(PROFILE, GAP, ctx).to_dict()
        b = pm.match_product(PROFILE, GAP, ctx).to_dict()
        self.assertEqual(a, b)

    def test_suggested_id_is_always_from_the_catalog(self):
        for ctx in (
            {"coverage_duration_pref": "temporary"},
            {"coverage_duration_pref": "lifetime", "cash_value_interest": "yes",
             "market_exposure_pref": "investment"},
        ):
            r = pm.match_product(PROFILE, GAP, ctx)
            if r.suggested_product_id is not None:
                self.assertIn(r.suggested_product_id, CATALOG_IDS)

    def test_coverage_is_reused_from_calculator_not_recomputed(self):
        r = pm.match_product(PROFILE, {"illustrative_gap": 737000},
                             {"coverage_duration_pref": "temporary"})
        self.assertEqual(r.estimated_additional_coverage, 737000)

    def test_reasons_are_present_and_tied_to_the_need(self):
        r = pm.match_product({**PROFILE, "term_years": 20}, GAP,
                             {"coverage_duration_pref": "temporary"})
        self.assertTrue(r.reasons)
        joined = " ".join(r.reasons)
        self.assertIn("temporary", joined.lower())
        self.assertIn("500,000", joined)  # ties the reason to the computed need


class TestOneQuestionAtATime(unittest.TestCase):

    def test_no_preferences_asks_duration_first(self):
        r = pm.match_product(PROFILE, GAP, {})
        self.assertIsNone(r.suggested_product_id)
        self.assertTrue(r.provisional)
        self.assertEqual(len(r.unresolved_questions), 1)
        self.assertIn("set number of years", r.unresolved_questions[0].lower())

    def test_question_progression_lifetime_path(self):
        s0 = pm.derive_signals(PROFILE, {})
        self.assertEqual(pm.next_matching_question(s0)["key"], "coverage_duration_pref")
        s1 = pm.derive_signals(PROFILE, {"coverage_duration_pref": "lifetime"})
        self.assertEqual(pm.next_matching_question(s1)["key"], "cash_value_interest")
        s2 = pm.derive_signals(PROFILE, {"coverage_duration_pref": "lifetime",
                                         "cash_value_interest": "yes"})
        self.assertEqual(pm.next_matching_question(s2)["key"], "market_exposure_pref")
        s3 = pm.derive_signals(PROFILE, {"coverage_duration_pref": "lifetime",
                                         "cash_value_interest": "yes",
                                         "market_exposure_pref": "index"})
        self.assertIsNone(pm.next_matching_question(s3))

    def test_known_info_is_reused_not_reasked(self):
        # Duration already known -> never ask the duration question again.
        s = pm.derive_signals(PROFILE, {"coverage_duration_pref": "temporary"})
        q = pm.next_matching_question(s)
        self.assertNotEqual(q and q["key"], "coverage_duration_pref")

    def test_partial_lifetime_gives_provisional_with_next_question(self):
        r = pm.match_product(PROFILE, GAP, {"coverage_duration_pref": "lifetime",
                                            "cash_value_interest": "yes"})
        self.assertEqual(r.suggested_product_id, "lifeline-indexed-protection")
        self.assertTrue(r.provisional)
        self.assertEqual(len(r.unresolved_questions), 1)


class TestGuardrails(unittest.TestCase):

    def test_no_push_when_no_additional_coverage(self):
        r = pm.match_product(PROFILE, {"illustrative_gap": 0},
                             {"coverage_duration_pref": "temporary", "term_years": 20})
        self.assertIsNone(r.suggested_product_id)
        self.assertTrue(r.no_additional_coverage)
        self.assertFalse(r.provisional)

    def test_negative_gap_also_no_push(self):
        r = pm.match_product(PROFILE, {"illustrative_gap": -50000},
                             {"coverage_duration_pref": "lifetime",
                              "cash_value_interest": "yes",
                              "market_exposure_pref": "index"})
        self.assertIsNone(r.suggested_product_id)
        self.assertTrue(r.no_additional_coverage)

    def test_no_premium_or_affordability_claim_in_output(self):
        r = pm.match_product(PROFILE, GAP, {
            "coverage_duration_pref": "temporary", "term_years": 20,
            "budget_comfort": "low"})
        blob = " ".join(r.reasons + r.assumptions + r.limitations).lower()
        for forbidden in ("per month", "premium is", "you can afford",
                          "affordable at", "$/mo", "monthly cost"):
            self.assertNotIn(forbidden, blob)
        # it should explicitly avoid confirming affordability
        self.assertTrue(any("afford" in a.lower() for a in r.assumptions))

    def test_insufficient_evidence_is_provisional_with_clarification(self):
        r = pm.match_product(PROFILE, GAP, {})
        self.assertTrue(r.provisional)
        self.assertTrue(r.unresolved_questions)

    def test_limitations_come_from_the_catalog_entry(self):
        r = pm.match_product({**PROFILE, "term_years": 20}, GAP,
                             {"coverage_duration_pref": "temporary"})
        # term products document that cover ends / no cash value
        joined = " ".join(r.limitations).lower()
        self.assertTrue("term" in joined or "cash value" in joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
