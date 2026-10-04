"""Gemini Live: server-side credential minting.

Gemini is the *voice* layer for Lifeline. It transcribes speech, drives the
conversation, and speaks the reply. It is NOT an authority for anything
financial:

  * Every needs amount comes from calculator.py.
  * Every piece of insurance education comes from the Bedrock Knowledge Base.
  * Session state, the assessment progression, and the guardrails all live in
    the orchestrator.

The frontend never sees the permanent GEMINI_API_KEY. It asks this module for
a short-lived *ephemeral* token scoped to a single Live API session, which is
what the browser SDK connects with. Minting happens server-side so the key is
never in client JavaScript, never in a bundle, and never in git.

Like bedrock_service.py, this module never raises when it is unconfigured: it
degrades to a safe no-op so the calculator, the API, and the whole test suite
keep working with no Google credentials present.

Verify with:
    from app.gemini_service import GeminiService
    GeminiService().mint_live_token()
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# A Live connection dies the moment the token expires, so keep the token's
# lifetime short. The browser fetches a fresh one per voice session.
DEFAULT_TOKEN_TTL_SECONDS = 1800

# Model used for the voice conversation. Kept in one place so it can be
# changed without touching code. This is the realtime, bidirectional-audio
# model, not the text model.
DEFAULT_LIVE_MODEL = "gemini-2.0-flash-live-001"


class GeminiUnavailable(RuntimeError):
    """Raised when a token is requested but Gemini cannot be configured."""


@dataclass(frozen=True)
class LiveToken:
    """A short-lived credential the browser can use to open a Live session."""

    token: str
    model: str
    expires_at: str  # ISO-8601 UTC

    def to_dict(self) -> dict:
        return {"token": self.token, "model": self.model, "expires_at": self.expires_at}


class GeminiService:
    """Mints ephemeral Gemini Live tokens from the server-side API key."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        # Read from the environment; never accept a key from the caller in a
        # request body, and never log it.
        self.api_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        self.model = (model or os.getenv("GEMINI_LIVE_MODEL", DEFAULT_LIVE_MODEL)).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def mint_live_token(self, ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS) -> LiveToken:
        """Return an ephemeral token the browser can use for one Live session.

        Scoped to `self.model` so a stolen token cannot be pointed at another
        model, and time-limited so it dies on its own.
        """
        if not self.enabled:
            raise GeminiUnavailable(
                "GEMINI_API_KEY is not configured on the server. Voice mode is "
                "unavailable; the text chat and the calculator still work."
            )
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - depends on install
            raise GeminiUnavailable(
                "google-genai is not installed in this environment."
            ) from exc

        now = datetime.now(timezone.utc)
        try:
            client = genai.Client(api_key=self.api_key)
            auth = client.auth_tokens.create(
                config=types.CreateAuthTokenConfig(
                    expire_time=now + timedelta(seconds=ttl_seconds),
                    new_session_expire_time=now + timedelta(seconds=ttl_seconds),
                    uses=1,  # single use: one token, one voice session
                    live_connect_constraints=types.LiveConnectConstraints(
                        model=self.model,
                    ),
                )
            )
        except Exception as exc:  # network/credential/auth failure
            # Never leak the key or the raw provider error to the client.
            raise GeminiUnavailable(
                f"Could not mint a Gemini Live token: {type(exc).__name__}."
            ) from exc

        token_value = getattr(auth, "name", None)
        if not token_value:
            raise GeminiUnavailable("Gemini returned an empty token.")

        return LiveToken(
            token=token_value,
            model=self.model,
            expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
        )