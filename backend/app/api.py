"""HTTP API for local development.

A dependency-free http.server app so the frontend can talk to the backend
without installing a web framework. The hosted Lambda is backend/index.mjs.
This module is the local Python engine, kept on the same calculation rules.

Endpoints:
    POST /api/turn           body: {session_id?, message?, profile_updates?, assumption_updates?}
    POST /api/submit-review  body: {session_id?, contact?} -> {ok, reference, ...}
    POST /api/gemini-token   body: {} -> {token, model, expires_at}
    GET  /health
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .orchestrator import Orchestrator
from .gemini_service import GeminiService, GeminiUnavailable

_orchestrator = Orchestrator()
_gemini = GeminiService()
_MAX_BODY = 1_000_000


def _allowed_origin(origin: str | None) -> str | None:
    """Allow configured site origins and local dev. Never reflect every origin."""
    if not origin:
        return None
    configured = [item.strip() for item in os.getenv("CORS_ORIGINS", "").split(",") if item.strip()]
    if origin in configured:
        return origin
    parsed = urlparse(origin)
    if parsed.scheme in ("http", "https") and parsed.hostname in ("localhost", "127.0.0.1"):
        return origin
    return None


def _cors_headers(origin: str | None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Vary": "Origin",
    }
    allowed = _allowed_origin(origin)
    if allowed:
        headers["Access-Control-Allow-Origin"] = allowed
    return headers


def _handle_turn(payload: dict) -> dict:
    return _orchestrator.handle_turn(
        session_id=payload.get("session_id"),
        message=payload.get("message", ""),
        profile_updates=payload.get("profile_updates"),
        assumption_updates=payload.get("assumption_updates"),
        memories=payload.get("memories"),
        path=payload.get("path"),
    )


def _handle_gemini_token(payload: dict) -> dict:
    """Mint a short-lived Gemini Live credential for the browser.

    The permanent GEMINI_API_KEY stays on the server; the browser only ever
    sees the short-lived token returned here.
    """
    try:
        return _gemini.mint_live_token().to_dict()
    except GeminiUnavailable as exc:
        return {"error": "unavailable", "detail": str(exc)}


def _handle_submit(payload: dict) -> dict:
    """Submit the collected assessment to a human advisor for review."""
    return _orchestrator.submit_for_review(
        session_id=payload.get("session_id"),
        contact=payload.get("contact", ""),
    )


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(code)
        for key, value in _cors_headers(self.headers.get("Origin")).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):  # CORS preflight
        self._send(204, {})

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/api/turn", "/api/gemini-token", "/api/submit-review"):
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send(400, {"error": "invalid JSON body"})
            return
        if length < 0 or length > _MAX_BODY:
            self._send(413, {"error": "request too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "invalid JSON body"})
            return
        try:
            if self.path == "/api/gemini-token":
                result = _handle_gemini_token(payload)
                # 503, not 500: the backend is healthy, voice is just off.
                self._send(200 if "error" not in result else 503, result)
            elif self.path == "/api/submit-review":
                result = _handle_submit(payload)
                self._send(200 if result.get("ok") else 503, result)
            else:
                self._send(200, _handle_turn(payload))
        except Exception:
            self._send(500, {"error": "internal error"})

    def log_message(self, *args):  # quieter console
        pass


def _lambda_response(status: int, result: dict, origin: str | None) -> dict:
    return {
        "statusCode": status,
        "headers": _cors_headers(origin),
        "body": json.dumps(result),
    }


def lambda_handler(event, context=None):
    """API Gateway entry for this Python engine. The hosted function is
    backend/index.mjs; this handler stays for local and test use."""
    headers = event.get("headers") or {}
    origin = headers.get("origin") or headers.get("Origin")
    raw = event.get("body") or ""
    if len(raw) > _MAX_BODY:
        return _lambda_response(413, {"error": "request too large"}, origin)
    try:
        body = json.loads(raw or "{}")
    except (ValueError, json.JSONDecodeError):
        return _lambda_response(400, {"error": "invalid JSON body"}, origin)

    # Determine the path from the proxy event (HTTP API v2 or REST v1 shapes).
    path = (event.get("rawPath")
            or event.get("path")
            or event.get("resource")
            or (event.get("requestContext", {}).get("http", {}) or {}).get("path", "")
            or "")
    try:
        if path.endswith("/gemini-token"):
            result = _handle_gemini_token(body)
            # 503, not 500: the backend is healthy, voice is just off.
            status = 200 if "error" not in result else 503
        elif path.endswith("/submit-review"):
            result = _handle_submit(body)
            status = 200 if result.get("ok") else 503
        else:
            result = _handle_turn(body)
            status = 200
    except Exception:
        return _lambda_response(500, {"error": "internal error"}, origin)

    return _lambda_response(status, result, origin)


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Lifeline backend on http://{host}:{port}  (POST /api/turn)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
