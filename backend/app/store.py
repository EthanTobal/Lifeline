"""Session persistence for Lifeline assessments.

Two interchangeable stores behind one interface:

    get_or_create(session_id) -> (session_id, Assessment)
    save(session_id, assessment)
    load(session_id)          -> Assessment | None   (read-only fetch)

* InMemorySessionStore  — process-local dict. Zero dependencies. Used for
  tests, local dev, and whenever DynamoDB isn't configured.
* DynamoDBSessionStore  — durable, encrypted-at-rest storage so a customer's
  collected profile survives Lambda recycling and can later be handed to a
  human advisor for review. boto3 is imported lazily so the rest of the app
  stays dependency-free.

The customer profile is PII (income, dependents, debts). It is stored in
DynamoDB (encrypted at rest, access-controlled by IAM), NEVER in a flat file.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .config import Config, load_config
from .models import Assessment


def _new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:12]}"


def _assessment_to_item(assessment: Assessment) -> dict:
    """Serialize an Assessment to a JSON-safe dict for storage."""
    return {
        "profile": assessment.profile,
        "context": assessment.context,
        "assumptions": assessment.assumptions,
    }


def _assessment_from_item(data: dict) -> Assessment:
    a = Assessment()
    a.profile = dict(data.get("profile", {}))
    a.context = dict(data.get("context", {}))
    if data.get("assumptions"):
        a.assumptions = dict(data["assumptions"])
    return a


class InMemorySessionStore:
    """Process-local store. Fine for tests and single-invocation local dev."""

    def __init__(self) -> None:
        self._sessions: dict[str, Assessment] = {}

    def get_or_create(self, session_id: str | None) -> tuple[str, Assessment]:
        if session_id and session_id in self._sessions:
            return session_id, self._sessions[session_id]
        new_id = session_id or _new_session_id()
        assessment = Assessment()
        self._sessions[new_id] = assessment
        return new_id, assessment

    def load(self, session_id: str) -> Assessment | None:
        return self._sessions.get(session_id)

    def save(self, session_id: str, assessment: Assessment) -> None:
        self._sessions[session_id] = assessment

    # extra fields (status/reference) are kept in memory keyed off session
    def save_meta(self, session_id: str, meta: dict) -> None:
        self._sessions.setdefault("__meta__" + session_id, meta)  # type: ignore


class DynamoDBSessionStore:
    """Durable store. Each item is one session's assessment plus metadata
    (status, reference id, timestamps). Store the whole assessment as a single
    JSON blob attribute so the schema can evolve without migrations.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self._table_name = config.assessments_table
        self._client = None

    def _ddb(self):
        if self._client is None:
            import boto3  # lazy
            if self.config.aws_profile:
                sess = boto3.Session(profile_name=self.config.aws_profile,
                                     region_name=self.config.aws_region)
            else:
                sess = boto3.Session(region_name=self.config.aws_region)
            self._client = sess.client("dynamodb")
        return self._client

    def get_or_create(self, session_id: str | None) -> tuple[str, Assessment]:
        if session_id:
            existing = self.load(session_id)
            if existing is not None:
                return session_id, existing
            # Keep the id the caller supplied even if it's new.
            return session_id, Assessment()
        return _new_session_id(), Assessment()

    def load(self, session_id: str) -> Assessment | None:
        try:
            resp = self._ddb().get_item(
                TableName=self._table_name,
                Key={"session_id": {"S": session_id}},
            )
        except Exception:
            return None
        item = resp.get("Item")
        if not item or "data" not in item:
            return None
        try:
            return _assessment_from_item(json.loads(item["data"]["S"]))
        except Exception:
            return None

    def save(self, session_id: str, assessment: Assessment,
             status: str = "in_progress", meta: dict | None = None) -> None:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        item: dict[str, Any] = {
            "session_id": {"S": session_id},
            "data": {"S": json.dumps(_assessment_to_item(assessment))},
            "status": {"S": status},
            "updated_at": {"S": now},
        }
        for k, v in (meta or {}).items():
            if v is not None:
                item[k] = {"S": str(v)}
        try:
            self._ddb().put_item(TableName=self._table_name, Item=item)
        except Exception:
            # Persistence failing must never break the conversation turn.
            pass


def build_store(config: Config | None = None):
    """Return the right store for the environment. DynamoDB when a table is
    configured; otherwise in-memory (tests / offline / local dev)."""
    cfg = config or load_config()
    if cfg.persistence_enabled:
        return DynamoDBSessionStore(cfg)
    return InMemorySessionStore()
