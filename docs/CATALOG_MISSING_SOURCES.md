# Product Catalog — Missing Source Material

The structured product catalog lives in `backend/data/demo_policies.json` and is
served read-only by `backend/services/policy_service.py`
(`get_policy(id)`, `filter_policies`, `get_policy_sources(id)`,
`unknown_fields(policy)`).

Every factual claim in the catalog cites a source in its entry's `sources` list.
The current entries are the four **fictional hackathon demo products**
(`lifeline-term-20`, `lifeline-term-30`, `lifeline-indexed-protection`,
`lifeline-variable-protection`), sourced from the Markdown in
`knowledge/demo-policies/`.

Those demo source files intentionally contain **no** eligibility rules,
availability, real pricing, or riders. Rather than invent them, those fields are
recorded as the string `"unknown"`. This note lists what the team must supply to
replace each `unknown` (and to add any real products).

## Fields currently `unknown` for every entry

| Field | Why it's unknown | Official material needed to fill it |
|---|---|---|
| `eligibility` | Demo source files state no issue-age, health, or underwriting rules | Issue-age ranges, underwriting class/health requirements, per-product eligibility — from the official product guide or rate card |
| `availability` | No state/channel availability in the demo files | State availability list and distribution channel, from the product's state-approval matrix |
| `costs.premium_detail` | No premiums or dollar figures exist in the demo files | Official rate tables / illustration output. NOTE: real premiums should come from a licensed illustration system, not be hand-entered |
| `optional_riders` | Demo files document no riders | The product's rider list, each with name, a plain-language summary, and `optional: true`, from the official rider brochure |
| `relative_cost` (permanent entries only) | Source states a relative-cost framing for term only | A sourced qualitative cost framing for IUL/VUL, if the team wants one — otherwise leave `null` |

## To add a REAL (non-demo) product

A real product entry must:

1. Set `is_demo_product: false`, `actual_lincoln_product: true`, and use the
   real insurer name in `insurer`. (The service's demo-safety validation
   currently requires the demo flags; it must be extended before a real product
   is added — see `_validate` in `policy_service.py`.)
2. Provide `plain_language`, `benefits`, `coverage_durations`, `eligibility`,
   `availability`, `limitations`, `risks`, `costs`, and `optional_riders` with
   **no `"unknown"`** left unexplained.
3. Cite an official source for every claim in `sources`, each as either:
   - `{"type": "official_url", "ref": "<https URL>", "section": "...", "supports": [...]}`, or
   - `{"type": "repo_document", "ref": "<path>", "section": "...", "supports": [...]}`
   pointing at the official PDF/page the claim comes from (document name + page).

## What the team should provide

- [ ] Official product fact sheets / product guides (PDF) for each real product,
      with page numbers, to replace the demo corpus if real products are wanted.
- [ ] Rate tables or an approved illustration source for any pricing claim.
- [ ] State-availability matrix per product.
- [ ] Rider brochures (names + plain-language summaries), each marked optional.
- [ ] Confirmation of the exact legal product names and the issuing insurer.

Until the above are supplied, the catalog remains **fictional demo products
only**, every `unknown` stays `unknown`, and nothing is invented.
