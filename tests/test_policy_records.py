"""Tests for the demo CUSTOMER POLICY RECORD lookup (the "I already have a
policy" path).

Every record is a FICTIONAL hackathon sample, not a real customer's policy.
These tests pin the security-sensitive behaviour: a valid demo id loads the
right record, an unknown id invents nothing, a bare policy number never
resolves, explanations stay grounded in the record, and the holder's riders
are not over-assumed from the product catalog.
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

from app import policy_records as pr  # noqa: E402
from app.orchestrator import Orchestrator, SessionStore  # noqa: E402


class TestRecordLookup(unittest.TestCase):

    def test_valid_demo_id_loads_the_correct_record(self):
        rec = pr.get_record("DEMO-TERM20-0001")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["id"], "DEMO-TERM20-0001")
        self.assertEqual(rec["coverage_amount"], 250000)
        self.assertEqual(rec["product_id"], "lifeline-term-20")

    def test_lookup_is_case_insensitive(self):
        self.assertIsNotNone(pr.get_record("demo-term20-0001"))

    def test_unknown_demo_id_returns_none_not_invented(self):
        self.assertIsNone(pr.get_record("DEMO-TERM20-9999"))
        self.assertIsNone(pr.get_record("DEMO-NOPE-0000"))

    def test_bare_policy_number_never_resolves(self):
        # The security guard: a number alone can never surface a record.
        for probe in ("100000123", "LL-93421", "12345", "", "   ",
                      "my policy", "term 20"):
            self.assertIsNone(pr.get_record(probe), probe)

    def test_find_policy_id_only_matches_strict_demo_form(self):
        self.assertEqual(
            pr.find_policy_id_in_text("here is DEMO-TERM20-0001 thanks"),
            "DEMO-TERM20-0001")
        self.assertIsNone(pr.find_policy_id_in_text("my number is 100000123"))
        self.assertIsNone(pr.find_policy_id_in_text("reference LL-93421"))
        self.assertIsNone(pr.find_policy_id_in_text("no id here at all"))

    def test_all_records_are_fictional_demo(self):
        for rid in pr.all_record_ids():
            rec = pr.get_record(rid)
            self.assertIs(rec["is_demo_record"], True)
            self.assertIs(rec["actual_customer_record"], False)
            self.assertEqual(rec["demo_disclaimer"], pr.REQUIRED_DISCLAIMER)


class TestExplanationGrounding(unittest.TestCase):

    def test_explanation_uses_the_records_actual_values(self):
        rec = pr.get_record("DEMO-TERM20-0001")
        text = pr.explain_record(rec)
        self.assertIn("$250,000", text)
        self.assertIn("20-year term", text)
        self.assertIn(rec["id"], text)
        # cites the source document + page
        self.assertIn("lifeline-term-20.md", text)
        self.assertIn("p. 1", text)
        # loudly labelled as a demo record
        self.assertIn("Fictional sample policy record", text)

    def test_riders_not_over_assumed_when_holder_has_none(self):
        rec = pr.get_record("DEMO-TERM20-0001")  # holds no riders
        self.assertEqual(rec["riders_held"], [])
        text = pr.explain_record(rec)
        self.assertIn("none recorded", text)
        # makes clear catalog riders are available, not necessarily held
        self.assertIn("available", text.lower())

    def test_only_the_riders_the_holder_actually_has_are_shown(self):
        rec = pr.get_record("DEMO-TERM30-0002")  # holds exactly one rider
        self.assertEqual(len(rec["riders_held"]), 1)
        text = pr.explain_record(rec)
        self.assertIn("Waiver of premium", text)

    def test_explanation_coverage_matches_record_for_each_id(self):
        # Grounding: the money figure in the explanation is the record's own.
        for rid in pr.all_record_ids():
            rec = pr.get_record(rid)
            text = pr.explain_record(rec)
            self.assertIn(f"{rec['coverage_amount']:,}", text,
                          f"{rid} explanation must quote its own coverage")


class TestOrchestratorPolicyLookup(unittest.TestCase):

    def _policy_session(self, orch):
        return orch.handle_turn(message="I already have a policy",
                                path="policy")["session_id"]

    def test_valid_id_in_conversation_explains_that_record(self):
        orch = Orchestrator(store=SessionStore())
        sid = self._policy_session(orch)
        resp = orch.handle_turn(session_id=sid,
                                message="please look up DEMO-TERM30-0002")
        self.assertEqual(resp["policy_lookup"]["status"], "found")
        self.assertEqual(resp["policy_lookup"]["id"], "DEMO-TERM30-0002")
        self.assertIn("$500,000", resp["assistant_message"])
        # grounded: never claims a benefit the record doesn't hold
        self.assertIn("Waiver of premium", resp["assistant_message"])

    def test_unknown_id_in_conversation_gives_useful_no_invention_reply(self):
        orch = Orchestrator(store=SessionStore())
        sid = self._policy_session(orch)
        resp = orch.handle_turn(session_id=sid,
                                message="look up DEMO-TERM20-9999")
        self.assertEqual(resp["policy_lookup"]["status"], "unknown")
        reply = resp["assistant_message"].lower()
        self.assertIn("couldn't find", reply)
        self.assertIn("won't guess", reply)
        # offers the valid sample ids rather than inventing one
        self.assertIn("demo-", reply)

    def test_bare_number_does_not_trigger_lookup_or_intake(self):
        orch = Orchestrator(store=SessionStore())
        sid = self._policy_session(orch)
        resp = orch.handle_turn(session_id=sid,
                                message="my policy number is 100000123")
        self.assertIsNone(resp["policy_lookup"])
        # policy path still never starts new-customer financial intake
        self.assertEqual(resp["assessment"]["status"], "idle")

    def test_lookup_does_not_start_financial_intake(self):
        orch = Orchestrator(store=SessionStore())
        sid = self._policy_session(orch)
        resp = orch.handle_turn(session_id=sid, message="DEMO-IUL-0003")
        self.assertEqual(resp["policy_lookup"]["status"], "found")
        self.assertEqual(resp["assessment"]["status"], "idle")
        self.assertIsNone(resp["assessment"]["next_field"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
