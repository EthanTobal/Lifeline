"""Isolated Amazon Bedrock service: Knowledge Base retrieval + grounded
model generation.

Deliberately the ONLY module that imports boto3. Everything else in the
backend runs without AWS. The Knowledge Base ID and model come from env
vars (see config.py), so the RAG developer can drop in the real values
later with no code change.

Clean interface the orchestrator depends on:
    retrieve_knowledge(query)                 -> list[Source]
    generate_grounded_response(query, context, sources) -> str

Guardrails baked in:
  * This module NEVER does arithmetic and NEVER overrides calculator output.
  * When Bedrock is not configured, calls return empty results instead of
    raising, so the app degrades gracefully offline.
  * Do NOT query S3 here — the documents are already ingested into the KB.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .config import Config, load_config


@dataclass
class Source:
    """A retrieved snippet from the Knowledge Base, for citation."""
    content: str
    location: str
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


# System instruction for the explain-only model. It can discuss the
# calculator's result and the retrieved Lincoln educational content, but it
# must not invent numbers, products, or guarantees.
GROUNDED_SYSTEM_PROMPT = (
    "You are Lifeline, a warm, patient assistant helping mostly older adults "
    "understand life insurance. Explain clearly, define any jargon, and keep a "
    "calm, reassuring tone.\n"
    "Hard rules:\n"
    "- You MUST NOT produce or change any dollar figure, coverage amount, or "
    "premium. Those come only from the deterministic calculator provided to you.\n"
    "- Describe results as an 'illustrative needs assessment based on the "
    "information provided', never as guaranteed advice or a quote.\n"
    "- Only state facts supported by the retrieved Lincoln educational content; "
    "if unsure, say so and suggest speaking to a licensed advisor.\n"
    "- Never claim a fictional LifeLine demo policy is a real Lincoln Financial "
    "product.\n"
    "- Never tell the user what to buy."
)


class BedrockService:
    """Thin wrapper around Bedrock Agent Runtime (retrieval) and Bedrock
    Runtime (model). Lazy-creates clients only when configured."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        self._agent_client = None
        self._runtime_client = None

    # ---- lazy clients (import boto3 only when actually used) ----
    def _session(self):
        import boto3  # local import keeps the rest of the app boto3-free

        if self.config.aws_profile:
            return boto3.Session(profile_name=self.config.aws_profile,
                                 region_name=self.config.aws_region)
        return boto3.Session(region_name=self.config.aws_region)

    def _agent(self):
        if self._agent_client is None:
            self._agent_client = self._session().client("bedrock-agent-runtime")
        return self._agent_client

    def _runtime(self):
        if self._runtime_client is None:
            self._runtime_client = self._session().client("bedrock-runtime")
        return self._runtime_client

    # ---- public interface ----
    def retrieve_knowledge(self, query: str, max_results: int = 5) -> list[Source]:
        """Retrieve relevant snippets from the Knowledge Base. Returns an
        empty list (never raises) when the KB is not configured."""
        if not self.config.kb_retrieval_enabled or not query.strip():
            return []
        try:
            resp = self._agent().retrieve(
                knowledgeBaseId=self.config.knowledge_base_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {"numberOfResults": max_results}
                },
            )
        except Exception:
            # Offline / creds / permissions: degrade to no sources.
            return []

        sources: list[Source] = []
        for item in resp.get("retrievalResults", []):
            content = (item.get("content") or {}).get("text", "")
            loc = item.get("location") or {}
            # location shape varies by data-source type; stringify defensively
            location = (
                (loc.get("s3Location") or {}).get("uri")
                or str(loc) if loc else "knowledge-base"
            )
            sources.append(Source(content=content, location=location,
                                  score=float(item.get("score", 0.0))))
        return sources

    def generate_grounded_response(
        self, query: str, context: str = "", sources: list[Source] | None = None
    ) -> str:
        """Ask the Bedrock model to EXPLAIN, grounded in the calculator
        `context` and retrieved `sources`. Returns "" when not configured so
        the caller can fall back to the calculator's own explanation."""
        if not self.config.bedrock_enabled:
            return ""

        sources = sources or []
        source_text = "\n\n".join(
            f"[Source {i + 1}] {s.content}" for i, s in enumerate(sources)
        ) or "(no knowledge-base snippets retrieved)"

        user_block = (
            f"Deterministic calculator result (authoritative — do not change the "
            f"numbers):\n{context or '(none)'}\n\n"
            f"Retrieved Lincoln educational content:\n{source_text}\n\n"
            f"User question: {query}\n\n"
            f"Explain in plain, reassuring language."
        )

        try:
            resp = self._runtime().converse(
                modelId=self.config.model_id,
                system=[{"text": GROUNDED_SYSTEM_PROMPT}],
                messages=[{"role": "user", "content": [{"text": user_block}]}],
                inferenceConfig={"maxTokens": 800, "temperature": 0.3},
            )
            parts = resp.get("output", {}).get("message", {}).get("content", [])
            return "".join(p.get("text", "") for p in parts).strip()
        except Exception:
            return ""
