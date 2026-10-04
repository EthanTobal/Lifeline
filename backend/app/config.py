"""Environment-variable configuration for the Lifeline backend.

Everything AWS-related is supplied through env vars so the Knowledge Base
ID and model can be dropped in later without code changes. Nothing is
hardcoded. If python-dotenv is installed, backend/.env is loaded first.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Load backend/.env if python-dotenv is available (optional dependency).
try:  # pragma: no cover - trivial import guard
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv not installed is fine; real env vars still work
    pass


@dataclass(frozen=True)
class Config:
    """Resolved backend configuration.

    `bedrock_enabled` lets the rest of the app degrade gracefully: when the
    Knowledge Base ID or model is not set, the Bedrock service returns empty
    results instead of raising, so the deterministic calculator and the API
    still work offline (useful for tests and local frontend development).
    """

    aws_region: str
    knowledge_base_id: str
    model_id: str
    aws_profile: str

    @property
    def bedrock_enabled(self) -> bool:
        return bool(self.knowledge_base_id and self.model_id)

    @property
    def kb_retrieval_enabled(self) -> bool:
        # Retrieval only needs the KB id; generation also needs the model.
        return bool(self.knowledge_base_id)


def load_config() -> Config:
    return Config(
        aws_region=os.getenv("AWS_REGION", "us-east-2"),
        knowledge_base_id=os.getenv("BEDROCK_KNOWLEDGE_BASE_ID", "").strip(),
        model_id=os.getenv("BEDROCK_MODEL_ID", "").strip(),
        aws_profile=os.getenv("AWS_PROFILE", "").strip(),
    )
