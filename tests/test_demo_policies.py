"""Tests for the LifeLine demo policy data layer.

Uses the Python standard library ``unittest`` so no third-party testing
dependency is introduced.

Coverage focus: the catalog loads, the four demo policies are retrievable and
correctly flagged as fictional, structural filtering works, and the S3
coordinates are present and well-formed.

Note: every policy here is a FICTIONAL hackathon demo product, not an actual
Lincoln Financial product, quote, offer, premium, or guarantee.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

# Allow `python -m unittest discover tests` or `pytest` from the repo root.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.services import policy_service  # noqa: E402

EXPECTED_IDS = {
    "lifeline-term-20",
    "lifeline-term-30",
    "lifeline-indexed-protection",
    "lifeline-variable-protection",
}

EXPECTED_S3_KEYS = {
    "lifeline-term-20": "policies/lifeline-term-20.pdf",
    "lifeline-term-30": "policies/lifeline-term-30.pdf",
    "lifeline-indexed-protection": "policies/lifeline-indexed-protection.pdf",
    "lifeline-variable-protection": "policies/lifeline-variable-protection.pdf",
}

REQUIRED_FIELDS = (
    "id", "display_name", "insurance_type", "is_demo_product",
    "actual_lincoln_product", "demo_disclaimer", "coverage_period",
    "premium_structure", "death_benefit", "cash_value", "relative_cost",
    "description", "tradeoffs", "matching_tags", "document",
)


class TestCatalogLoading(unittest.TestCase):

    def test_loads_all_four_policies(self):
        policies = policy_service.get_all_policies()
        self.assertEqual(len(policies), 4)
        self.assertEqual({p["id"] for p in policies}, EXPECTED_IDS)

    def test_catalog_file_is_valid_json_with_policies_list(self):
        with open(policy_service.DATA_PATH, encoding="utf-8") as handle:
            catalog = json.load(handle)
        self.assertIsInstance(catalog["policies"], list)
        self.assertEqual(len(catalog["policies"]), 4)

    def test_every_policy_has_required_fields(self):
        for policy in policy_service.get_all_policies():
            for field in REQUIRED_FIELDS:
                self.assertIn(field, policy,
                              f"{policy.get('id')} missing field {field}")


class TestGetPolicyById(unittest.TestCase):

    def test_get_lifeline_term_20(self):
        policy = policy_service.get_policy("lifeline-term-20")
        self.assertIsNotNone(policy)
        self.assertEqual(policy["display_name"], "LifeLine Term 20")
        self.assertEqual(policy["insurance_type"], "term")
        self.assertEqual(policy["coverage_period"], "20 years")

    def test_get_lifeline_term_30(self):
        policy = policy_service.get_policy("lifeline-term-30")
        self.assertIsNotNone(policy)
        self.assertEqual(policy["display_name"], "LifeLine Term 30")
        self.assertEqual(policy["coverage_period"], "30 years")

    def test_get_lifeline_indexed_protection(self):
        policy = policy_service.get_policy("lifeline-indexed-protection")
        self.assertIsNotNone(policy)
        self.assertEqual(policy["display_name"], "LifeLine Indexed Protection")
        self.assertEqual(policy["insurance_type"], "indexed_universal_life")

    def test_get_lifeline_variable_protection(self):
        policy = policy_service.get_policy("lifeline-variable-protection")
        self.assertIsNotNone(policy)
        self.assertEqual(policy["display_name"], "LifeLine Variable Protection")
        self.assertEqual(policy["insurance_type"], "variable_universal_life")

    def test_unknown_policy_id_returns_none(self):
        self.assertIsNone(policy_service.get_policy("no-such-policy"))
        self.assertIsNone(policy_service.get_policy(""))

    def test_unknown_policy_has_no_document(self):
        self.assertIsNone(policy_service.get_policy_document("no-such-policy"))
        self.assertIsNone(policy_service.get_document_location("no-such-policy"))


class TestFiltering(unittest.TestCase):

    def test_filter_by_term_returns_two_term_policies(self):
        result = policy_service.filter_policies(insurance_type="term")
        self.assertEqual(len(result), 2)
        self.assertEqual({p["id"] for p in result},
                         {"lifeline-term-20", "lifeline-term-30"})

    def test_filter_by_permanent_types(self):
        iul = policy_service.filter_policies(
            insurance_type="indexed_universal_life")
        vul = policy_service.filter_policies(
            insurance_type="variable_universal_life")
        self.assertEqual([p["id"] for p in iul], ["lifeline-indexed-protection"])
        self.assertEqual([p["id"] for p in vul], ["lifeline-variable-protection"])

    def test_filter_by_matching_tags(self):
        result = policy_service.filter_policies(
            matching_tags=["permanent_coverage", "cash_value"])
        self.assertEqual({p["id"] for p in result},
                         {"lifeline-indexed-protection",
                          "lifeline-variable-protection"})

    def test_filter_tags_requires_all_tags(self):
        result = policy_service.filter_policies(
            matching_tags=["index_linked", "market_exposure"])
        self.assertEqual(result, [])

    def test_filter_combines_type_and_tags(self):
        result = policy_service.filter_policies(
            insurance_type="term", matching_tags=["dependents"])
        self.assertEqual(len(result), 2)

    def test_no_filters_returns_all(self):
        self.assertEqual(len(policy_service.filter_policies()), 4)

    def test_get_all_tags_includes_shared_tags(self):
        tags = policy_service.get_all_tags()
        # Note: "term" is an insurance_type, not a matching tag, so it is not
        # expected here. Tags come only from each policy's matching_tags list.
        for expected in ("cash_value", "dependents", "mortgage",
                         "permanent_coverage", "temporary_coverage",
                         "index_linked", "market_exposure", "flexibility",
                         "long_term", "simplicity"):
            self.assertIn(expected, tags)


class TestDemoGuardrails(unittest.TestCase):
    """The demo-product contract must hold for every policy."""

    def test_all_policies_are_demo_products(self):
        for policy in policy_service.get_all_policies():
            self.assertIs(policy["is_demo_product"], True,
                          f"{policy['id']} must be a demo product")

    def test_no_policy_claims_to_be_a_lincoln_product(self):
        for policy in policy_service.get_all_policies():
            self.assertIs(policy["actual_lincoln_product"], False,
                          f"{policy['id']} must not claim to be a Lincoln product")

    def test_all_policies_carry_required_disclaimer(self):
        expected = ("Hackathon Demo — Not an actual Lincoln Financial "
                    "product or quote.")
        for policy in policy_service.get_all_policies():
            self.assertEqual(policy["demo_disclaimer"], expected,
                             f"{policy['id']} disclaimer mismatch")

    def test_no_credential_or_url_fields_present(self):
        """S3 coordinates only; no credentials and no fabricated URLs."""
        for policy in policy_service.get_all_policies():
            document = policy["document"]
            self.assertNotIn("url", document)
            self.assertNotIn("presigned_url", document)
            blob = json.dumps(policy).lower()
            for forbidden in ("aws_access_key", "secret_access_key",
                              "password", "http://", "https://"):
                self.assertNotIn(forbidden, blob)


class TestPolicyDocuments(unittest.TestCase):

    def test_catalog_does_not_embed_an_account_bucket(self):
        for policy in policy_service.get_all_policies():
            document = policy["document"]
            self.assertTrue(document["s3_key"])
            self.assertNotIn("714047902595", json.dumps(document))

    def test_every_policy_has_s3_key(self):
        for policy in policy_service.get_all_policies():
            self.assertTrue(policy["document"]["s3_key"])

    def test_every_s3_key_starts_with_policies_prefix(self):
        for policy in policy_service.get_all_policies():
            self.assertTrue(
                policy["document"]["s3_key"].startswith("policies/"),
                f"{policy['id']} key must start with 'policies/'")

    def test_s3_mappings_are_exact(self):
        for policy_id, expected_key in EXPECTED_S3_KEYS.items():
            location = policy_service.get_document_location(policy_id)
            self.assertIsNotNone(location)
            self.assertEqual(location["s3_bucket"], os.environ.get("DOCUMENT_BUCKET", "").strip())
            self.assertNotIn("714047902595", location["s3_bucket"])
            self.assertEqual(location["s3_key"], expected_key)

    def test_documents_are_marked_available(self):
        for policy in policy_service.get_all_policies():
            self.assertTrue(policy["document"]["available"])
            self.assertTrue(policy["document"]["title"])


class TestNoFabricatedFinancials(unittest.TestCase):
    """The source files contain no pricing; none may appear here."""

    def test_relative_cost_only_where_source_states_it(self):
        for policy in policy_service.get_all_policies():
            if policy["insurance_type"] == "term":
                self.assertTrue(policy["relative_cost"])
            else:
                self.assertIsNone(
                    policy["relative_cost"],
                    f"{policy['id']} must not invent relative cost")

    def test_no_currency_amounts_present(self):
        for policy in policy_service.get_all_policies():
            blob = json.dumps(policy)
            for symbol in ("$", "USD ", "per month", "per year"):
                self.assertNotIn(symbol, blob,
                                 f"{policy['id']} contains fabricated pricing")


class TestStructuredCatalogSchema(unittest.TestCase):
    """The enriched catalog schema: every entry carries the structured fields
    a recommendation/comparison layer needs, every factual claim is sourced,
    and anything undocumented is explicitly 'unknown' rather than invented."""

    STRUCTURED_FIELDS = (
        "insurer", "policy_type", "plain_language", "benefits",
        "coverage_durations", "eligibility", "availability", "limitations",
        "risks", "costs", "optional_riders", "sources",
    )

    def test_every_entry_retrievable_by_id_with_full_schema(self):
        for policy_id in EXPECTED_IDS:
            policy = policy_service.get_policy(policy_id)
            self.assertIsNotNone(policy, f"{policy_id} not retrievable by ID")
            for field in self.STRUCTURED_FIELDS:
                self.assertIn(field, policy,
                              f"{policy_id} missing structured field {field}")

    def test_identity_fields_present(self):
        for policy in policy_service.get_all_policies():
            self.assertTrue(policy["id"])
            self.assertTrue(policy["display_name"])
            self.assertTrue(policy["insurer"])
            self.assertTrue(policy["policy_type"])

    def test_insurer_is_not_a_real_company(self):
        for policy in policy_service.get_all_policies():
            self.assertNotIn("lincoln", policy["insurer"].lower(),
                             f"{policy['id']} must not name Lincoln as issuer")

    def test_every_policy_cites_at_least_one_source_with_a_ref(self):
        for policy in policy_service.get_all_policies():
            sources = policy_service.get_policy_sources(policy["id"])
            self.assertTrue(sources, f"{policy['id']} must cite a source")
            for source in sources:
                self.assertTrue(source.get("ref"),
                                f"{policy['id']} source missing 'ref'")

    def test_source_refs_point_at_real_repo_documents(self):
        for policy in policy_service.get_all_policies():
            for source in policy_service.get_policy_sources(policy["id"]):
                if source.get("type") == "repo_document":
                    path = os.path.join(REPO_ROOT, source["ref"])
                    self.assertTrue(os.path.exists(path),
                                    f"{policy['id']} cites missing doc {source['ref']}")

    def test_undocumented_details_are_explicit_unknown_not_invented(self):
        # The source demo files document no eligibility, availability, real
        # premiums, or riders -- these must read 'unknown', never a guess.
        for policy in policy_service.get_all_policies():
            self.assertEqual(policy["eligibility"], "unknown")
            self.assertEqual(policy["availability"], "unknown")
            self.assertEqual(policy["optional_riders"], "unknown")
            self.assertEqual(policy["costs"]["premium_detail"], "unknown")

    def test_unknown_fields_helper_reports_the_unknowns(self):
        for policy in policy_service.get_all_policies():
            unknown = policy_service.unknown_fields(policy)
            for expected in ("eligibility", "availability", "optional_riders",
                             "costs.premium_detail"):
                self.assertIn(expected, unknown,
                              f"{policy['id']} should flag {expected} unknown")

    def test_benefits_and_limitations_are_nonempty_lists(self):
        for policy in policy_service.get_all_policies():
            self.assertIsInstance(policy["benefits"], list)
            self.assertTrue(policy["benefits"], f"{policy['id']} has no benefits")
            self.assertIsInstance(policy["limitations"], list)
            self.assertTrue(policy["limitations"],
                            f"{policy['id']} has no limitations")

    def test_coverage_durations_is_a_nonempty_list(self):
        for policy in policy_service.get_all_policies():
            self.assertIsInstance(policy["coverage_durations"], list)
            self.assertTrue(policy["coverage_durations"])


class TestCatalogCustomerSeparation(unittest.TestCase):
    """Product catalog data must stay separate from any individual customer's
    policy records and from the assessment profile."""

    def test_catalog_has_no_customer_record_fields(self):
        customer_fields = (
            "customer_id", "customer", "owner", "policy_number", "annual_income",
            "mortgage_balance", "beneficiary", "ssn", "profile", "session_id",
        )
        for policy in policy_service.get_all_policies():
            keys = {k.lower() for k in policy.keys()}
            for forbidden in customer_fields:
                self.assertNotIn(forbidden, keys,
                                 f"{policy['id']} leaks a customer field {forbidden}")

    def test_catalog_module_does_not_import_customer_models(self):
        # The retrieval layer must not reach into the customer assessment layer.
        import inspect
        src = inspect.getsource(policy_service)
        self.assertNotIn("from app.models", src)
        self.assertNotIn("import models", src)
        self.assertNotIn("Assessment", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)