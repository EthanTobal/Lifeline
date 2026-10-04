"""HTTP API for local development.

A dependency-free http.server app so the frontend can talk to the backend
without installing a web framework. For AWS, lambda_handler (below) wraps
the same Orchestrator behind API Gateway — build now, deploy later.

Endpoints:
    POST /api/turn           body: {session_id?, message?, profile_updates?, assumption_updates?}
    POST /api/submit-review  body: {session_id?, contact?} -> {ok, reference, ...}
    POST /api/gemini-token   body: {} -> {token, model, expires_at}
    GET  /health
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .orchestrator import Orchestrator
from .gemini_service import GeminiService, GeminiUnavailable

_orchestrator = Orchestrator()
_gemini = GeminiService()


def _handle_turn(payload: dict) -> dict:
    return _orchestrator.handle_turn(
        session_id=payload.get("session_id"),
        message=payload.get("message", ""),
        profile_updates=payload.get("profile_updates"),
        assumption_updates=payload.get("assumption_updates"),
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
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")  # demo only
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
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
                self._send(200, _handle_submit(payload))
            else:
                self._send(200, _handle_turn(payload))
        except Exception as exc:  # never leak a stack trace to the client
            self._send(500, {"error": "internal error", "detail": str(exc)})

    def log_message(self, *args):  # quieter console
        pass


def lambda_handler(event, context=None):
    """AWS Lambda entry point (API Gateway proxy integration). Routes by path
    so /api/turn, /api/submit-review, and /api/gemini-token all hit the same
    backend."""
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, json.JSONDecodeError):
        return {"statusCode": 400,
                "headers": {"Content-Type": "application/json",
                            "Access-Control-Allow-Origin": "*"},
                "body": json.dumps({"error": "invalid JSON body"})}

    # Determine the path from the proxy event (HTTP API v2 or REST v1 shapes).
    path = (event.get("rawPath")
            or event.get("path")
            or event.get("resource")
            or (event.get("requestContext", {}).get("http", {}) or {}).get("path", "")
            or "")
    if path.endswith("/gemini-token"):
        result = _handle_gemini_token(body)
        # 503, not 500: the backend is healthy, voice is just off.
        status = 200 if "error" not in result else 503
    elif path.endswith("/submit-review"):
        result = _handle_submit(body)
        status = 200
    else:
        result = _handle_turn(body)
        status = 200

    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(result),
    }


def main(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Lifeline backend on http://{host}:{port}  (POST /api/turn)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
