# Lifeline Backend API

How the frontend and voice layers talk to the backend brain. One endpoint
does everything: it tracks the customer's assessment, runs the deterministic
calculator when there's enough info, retrieves Lincoln content from the
Bedrock Knowledge Base, and returns a plain-language explanation.

- **Calculations are deterministic code**, never the model. Numbers are always correct.
- **The model only explains** — grounded in the Knowledge Base, in warm, jargon-free language.
- Works offline too: if Bedrock isn't configured, you still get the calculator's own explanation (and `sources: []`).

---

## Running it locally

```bash
cd backend
copy .env.example .env        # Windows (use `cp` on mac/linux); fill in values
pip install -r requirements.txt   # only needed for live AWS calls
python app/api.py             # serves http://127.0.0.1:8000
```

Config comes from `backend/.env` (never committed):

| Variable | Purpose | Current value |
| --- | --- | --- |
| `AWS_REGION` | Region for Bedrock | `us-east-2` |
| `BEDROCK_KNOWLEDGE_BASE_ID` | The Lincoln KB | `E9CJNNHXLT` |
| `BEDROCK_MODEL_ID` | Explain-only model | `us.amazon.nova-lite-v1:0` |
| `AWS_PROFILE` | Credentials profile | `lifeline` |

---

## Endpoint

### Saved memories

The Saved memories button lets the customer explicitly add, edit, delete, or
clear personal notes. Up to 50 notes of 500 characters each are retained in
this browser under `lifeline-memories`, including notes from the old Gemini
demo. They survive new chats and reloads; they are visible to anyone using
the same browser. Nothing is automatically saved from a conversation.

Every text or voice turn can include an optional `memories` array of strings
in the `/api/turn` request. The backend bounds and validates the notes before
passing them to Bedrock as unconfirmed personal context. They are not copied
into the server assessment or used as calculator inputs. Deleting a note
excludes it from subsequent requests; it does not undo information previously
shared in a conversation. The deterministic offline fallback does not use
these notes to personalize replies.

Deploy the updated backend along with the frontend to enable memory context
in live replies. Existing backend deployments ignore this optional field.

### `POST /api/turn`

Call this once per user turn (a typed message, a spoken utterance turned to
text, or a batch of profile answers from a form). Send only what's new — the
backend remembers the rest by `session_id`.

**Request body** (all fields optional except on the first call you'll usually send a `message`):

```json
{
  "session_id": "sess_ab12cd34",
  "message": "How much life insurance do I need?",
  "profile_updates": {
    "annual_income": 80000,
    "num_children": 2,
    "mortgage_balance": 200000,
    "non_mortgage_debt": 30000,
    "existing_coverage": 100000,
    "liquid_savings": 50000
  },
  "assumption_updates": {
    "income_replacement_years": 12
  }
}
```

- **`session_id`** — omit on the first call; use the one returned to you on every call after.
- **`message`** — the user's words. Drives the KB retrieval and the explanation.
- **`profile_updates`** — the six needs-assessment inputs you've collected (see above). Partial is fine; send them as you get them. Unknown keys are ignored.
- **`assumption_updates`** — lets the user change an assumption and recalculate (e.g. years of income to replace). Optional.

The guided assessment collects exactly six profile fields. Do **not** collect underwriting or pricing inputs (`age`, `sex`, `smoker`, `health`, `term_years`) — LifeLine does not quote, underwrite, or assess eligibility, and none of them affect the published `gross_need` or `illustrative_gap`.

**Response body** (the contract — safe to code against):

```json
{
  "session_id": "sess_ab12cd34",
  "assistant_message": "Based on what you've told me, your family would likely need about $1,095,000...",
  "assessment": {
    "status": "collecting | ready",
    "missing_fields": ["annual_income"],
    "profile": { "...": "what we know so far" },
    "context": { "affordability_monthly": 50, "name": "Margaret" },
    "assumptions": { "income_replacement_years": 10, "education_per_child": 100000, "...": "..." },
    "field_help": { "annual_income": "Drives how much income your family would need to replace." }
  },
  "needs_assessment": {
    "illustrative_gap": 1095000,
    "breakdown": {
      "components": [
        { "key": "debt", "label": "Other debt", "detail": "Credit cards, car and student loans: $30,000", "amount": 30000 },
        { "key": "final_expenses", "label": "Final expenses", "detail": "Funeral and final medical costs, added for you as an editable assumption", "amount": 15000 },
        { "key": "income", "label": "Income replacement", "detail": "$80,000 per year x 10 years (editable assumption)", "amount": 800000 },
        { "key": "mortgage", "label": "Mortgage", "detail": "Remaining mortgage balance to pay off", "amount": 200000 },
        { "key": "education", "label": "Children's education", "detail": "2 child(ren) x $100,000 per child (editable assumption)", "amount": 200000 }
      ],
      "offsets": [
        { "key": "existing_coverage", "label": "Existing life insurance", "amount": 100000 },
        { "key": "liquid_savings", "label": "Savings & liquid assets", "amount": 50000 }
      ],
      "gross_need": 1245000,
      "total_offsets": 150000
    },
    "assumptions": { "income_replacement_years": 10, "...": "..." },
    "disclaimer": "This is an illustrative needs assessment..."
  },
  "sources": [
    { "content": "retrieved Lincoln snippet...", "location": "s3://.../documents/...", "score": 0.40 }
  ],
  "disclaimer": "This is an illustrative needs assessment based on the information you provided..."
}
```

### How to use each part in the UI

- **`assistant_message`** — show this as the assistant's chat bubble (or feed to text-to-speech for voice). Already plain-language and caring.
- **`assessment.status`**:
  - `collecting` → keep asking questions; `missing_fields` tells you what's still required. The guided assessment collects six fields in this order: `annual_income`, `num_children`, `mortgage_balance`, `non_mortgage_debt`, `existing_coverage`, `liquid_savings`. Age is **not** required — it does not affect `gross_need` or `illustrative_gap`.
  - `ready` → a full `needs_assessment` is included; render the result card.
- **`needs_assessment.illustrative_gap`** — the headline number. `null` until status is `ready`.
- **`needs_assessment.breakdown`** — render the itemized rows + offsets so the user sees *how* the number was reached (transparency = trust).
- **`assessment.assumptions`** — send changes back via `assumption_updates` to recalculate through the backend. The response carries the full set for transparency, but display only the three that drive the published result: `income_replacement_years`, `education_per_child`, `final_expenses`. The HLV parameters (`hlv_discount_rate`, `hlv_income_growth`, `hlv_personal_consumption`, `retirement_age`) and `income_multiple_low`/`high`/`max_coverage` belong to internal calculations that are not part of the customer-facing experience, so they are not displayed. Use `field_help` for the one-line "why it matters" text on the three you do show.
- **`sources`** — optional "where this came from" citations. Empty when the KB isn't configured.
- **`disclaimer`** — show it near any dollar figure. Always "illustrative", never a quote.

### What this API will never send you

- **No premium, rate, or monthly price.** LifeLine is not a quoting service, so `premium_estimate` is deliberately absent from the response contract and is never passed to the model. If a customer asks what something costs, `assistant_message` declines and the assessment continues. Do not synthesise a price in the UI.
- The internal HLV, 10–15x sanity band, and validation `flags` are also not published. Render `components`, `offsets`, `gross_need`, and `assumptions`.

---

## Health check

### `GET /health` → `{ "status": "ok" }`

---

## Notes for integration

- **CORS** is open (`*`) for local dev; tighten before any real deployment.
- **Voice**: convert speech → text, send as `message`; take `assistant_message` → text-to-speech. The backend is voice-agnostic.
- **AWS Lambda**: `app/api.py` also exposes `lambda_handler` (API Gateway proxy) wrapping the same logic — build-ready, not yet deployed.
- **Sessions** are in memory for the demo. Swap `SessionStore` in `orchestrator.py` for a DynamoDB-backed store for production (same interface) — do not persist customer data to flat files.

## Guardrails (enforced in the backend)

- The model never independently calculates financial figures. All needs-assessment amounts originate from the deterministic calculator; the model may explain those calculator-generated results.
- No premium, rate, or monthly price is calculated, returned, or shown. Pricing questions are declined and the assessment continues.
- Affordability is stored as context and never changes the calculated need.
- Assumptions are explicit, returned, and editable — nothing is silently invented.
- Fictional "LifeLine" demo policies are never presented as real Lincoln products.
