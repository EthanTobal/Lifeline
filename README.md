# Lifeline

Lifeline is a life-insurance needs conversation. The application asks the questions and does the arithmetic. A model may explain those results. It does not invent coverage numbers, prices, or approval decisions.

## What is deployed

The hosted API is [`backend/index.mjs`](backend/index.mjs), on the Lambda function updated by [`backend/deploy.ps1`](backend/deploy.ps1). The static site is [`frontend/`](frontend/) and is published by [`frontend/deploy.ps1`](frontend/deploy.ps1).

[`docs/BACKEND_API.md`](docs/BACKEND_API.md) describes that deployed contract.

## Local Python engine

[`backend/app`](backend/app) is a second implementation used by the pytest suite and by `python app/api.py`. It follows the same customer-facing rules as the hosted handler:

- a spouse is not a child
- a skipped answer is not zero, and it does not publish a coverage gap
- money is rounded to the nearest dollar
- two unclear replies leave the question instead of looping
- advisor review says it saved something only when the save succeeded

The Python engine still matches a four-product catalog. The hosted handler suggests term or permanent cover from the words the person used. That product difference is intentional until one engine is removed.

## Configuration

Copy `backend/.env.example` to `backend/.env` and `frontend/config.example.js` to `frontend/config.js`. Neither file is committed. Deploy scripts read `backend/.env` for the bucket, distribution, and API origin.

Set `SESSION_SECRET` on the Lambda so session tokens are signed. Set `ASSESSMENTS_TABLE` before the review button can save an assessment. Set `CORS_ORIGINS` to the site origin. Localhost is allowed without that list.

## Tests

```bash
npm test
python -m pytest tests -q
```
