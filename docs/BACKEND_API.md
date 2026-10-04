# Lifeline Backend API

The hosted API is `backend/index.mjs`. The Python package under `backend/app` is the local engine and the pytest target. This document describes the hosted contract.

Calculations are deterministic code. The model may explain a result. It does not produce the coverage figure.

## Running the hosted handler's tests

```bash
npm test
```

Local Python API, for the pytest engine:

```bash
cd backend
cp .env.example .env
python app/api.py
```

That server is not what `backend/deploy.ps1` publishes.

## Configuration

Copy `backend/.env.example` to `backend/.env`. Leave a value empty when you do not have it. The calculator still runs.

| Variable | Purpose |
| --- | --- |
| `AWS_REGION` | Region for Bedrock and storage |
| `BEDROCK_KNOWLEDGE_BASE_ID` | Retrieval. Empty disables it |
| `BEDROCK_MODEL_ID` | Explanation model |
| `DOCUMENT_BUCKET` | S3 bucket for the document library |
| `SESSION_SECRET` | HMAC key for the session token |
| `CORS_ORIGINS` | Comma-separated browser origins |
| `ASSESSMENTS_TABLE` | DynamoDB table for advisor review |
| `AGENT_REVIEW_TOPIC_ARN` | SNS topic for advisor notification |

The browser API origin lives in `frontend/config.js` as `window.LIFELINE_API_BASE`.

## `POST /api/turn`

```json
{
  "session_id": "s2....",
  "message": "How much life insurance do I need?",
  "path": "coverage",
  "profile_updates": { "annual_income": 80000 },
  "assumption_updates": { "income_replacement_years": 12 },
  "memories": ["Lives in Ohio"]
}
```

- `session_id` — omit on the first call. Later calls send the token from the previous response. When `SESSION_SECRET` is set the token is `s2.<payload>.<signature>`. A modified token is ignored and the conversation starts over.
- `path` — `coverage`, `policy`, or `general`.
- `profile_updates` — any of the six inputs. Unknown keys are ignored.
- `assumption_updates` — `income_replacement_years`, `education_per_child`, `final_expenses`.
- `memories` — up to 50 notes, 500 characters each. They are not calculator inputs.

The six inputs, in order: `annual_income`, `num_children`, `mortgage_balance`, `non_mortgage_debt`, `existing_coverage`, `liquid_savings`.

An explicit zero is a real answer. A skip is not. Skipped fields stay blank, are not counted as zero, and the response stays `collecting` with `illustrative_gap: null` until every field has a number.

A spouse, wife, husband, or partner is not a child. Only children change the education line.

```json
{
  "session_id": "s2....",
  "assistant_message": "Based on what you shared...",
  "path": "coverage",
  "assessment": {
    "status": "collecting",
    "missing_fields": ["annual_income"],
    "next_field": "annual_income",
    "profile": {},
    "assumptions": {
      "income_replacement_years": 10,
      "education_per_child": 100000,
      "final_expenses": 15000
    },
    "skipped_fields": []
  },
  "needs_assessment": {
    "illustrative_gap": null,
    "breakdown": {},
    "assumptions": {}
  },
  "disclaimer": "This is an illustrative needs assessment..."
}
```

`assessment.status` is `idle`, `collecting`, or `ready`. A gap is present only when status is `ready`. Amounts are whole dollars. `$500` stays `$500`.

The worked example: income $80,000, 2 children, mortgage $200,000, other debt $30,000, existing cover $100,000, savings $50,000. Gross need $1,245,000. Offsets $150,000. Gap $1,095,000.

There is no premium in the response. A pricing question is declined.

## `POST /api/submit-review`

```json
{ "session_id": "s2....", "contact": "person@example.com" }
```

When `ASSESSMENTS_TABLE` is set, a successful save returns `ok: true`, a `LL-` reference, and `advisor_notified` true only if SNS accepted the notice. The message matches those facts.

When storage is not configured, or the save fails, the response is HTTP 503 with `ok: false` and an error that says the estimate was not sent.

## `GET /health`

`{ "status": "ok" }`

## CORS

`Access-Control-Allow-Origin` is the request origin when it is listed in `CORS_ORIGINS`, or when it is localhost or `127.0.0.1`. Other origins are not reflected.

## Voice

`POST /api/gemini-token` on this Lambda returns 404. Voice is not part of the hosted text handler.
