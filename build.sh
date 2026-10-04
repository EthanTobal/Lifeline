#!/usr/bin/env bash
# Start the Lifeline site and the same API the hosted function runs.
#
#   ./build.sh
#
# Then open http://127.0.0.1:8080
# Ctrl-C stops it.
#
# backend/.env is loaded when that file exists. Bedrock is used only when
# this machine has credentials. The calculator still runs without them.

set -euo pipefail
cd "$(dirname "$0")"

if [[ -f backend/.env ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    [[ "$line" == *=* ]] || continue
    name="${line%%=*}"
    value="${line#*=}"
    name="${name#"${name%%[![:space:]]*}"}"
    name="${name%"${name##*[![:space:]]}"}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
    if [[ -n "$name" && -z "${!name:-}" ]]; then
      export "$name=$value"
    fi
  done < backend/.env
fi

if [[ -n "${AWS_PROFILE:-}" ]]; then
  aws_config="${HOME}/.aws/config"
  if [[ ! -f "$aws_config" ]] || ! grep -Eq "^\\[(profile )?${AWS_PROFILE}\\]" "$aws_config"; then
    echo "AWS profile ${AWS_PROFILE} is not configured here. Model calls use the default credentials when those exist."
    unset AWS_PROFILE
  fi
fi

export PORT="${PORT:-8080}"

exec node --input-type=module <<'EOF'
import { randomBytes } from "node:crypto";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { pathToFileURL } from "node:url";

if (!process.env.SESSION_SECRET) {
  process.env.SESSION_SECRET = randomBytes(32).toString("base64url");
  console.log("Using a temporary session secret for this process.");
}

const { handler } = await import(pathToFileURL(resolve("backend/index.mjs")).href);
const root = resolve("frontend");
const port = Number(process.env.PORT) || 8080;
const types = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

function localConfig() {
  return Buffer.from('window.LIFELINE_API_BASE = "";\n');
}

async function serveFile(urlPath, response) {
  const requested = urlPath === "/" ? "/index.html" : urlPath;
  if (requested === "/config.js") {
    response.writeHead(200, {
      "Content-Type": "text/javascript; charset=utf-8",
      "Cache-Control": "no-cache",
    });
    response.end(localConfig());
    return;
  }
  const filePath = normalize(join(root, requested));
  if (filePath !== root && !filePath.startsWith(root + "/")) {
    response.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("forbidden");
    return;
  }
  try {
    const body = await readFile(filePath);
    const type = types[extname(filePath).toLowerCase()] || "application/octet-stream";
    response.writeHead(200, { "Content-Type": type, "Cache-Control": "no-cache" });
    response.end(body);
  } catch {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("not found");
  }
}

function readRequest(request) {
  return new Promise((resolveBody, reject) => {
    const chunks = [];
    let size = 0;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > 1_000_000) {
        reject(Object.assign(new Error("too large"), { statusCode: 413 }));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => resolveBody(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url || "/", "http://127.0.0.1");
  const path = url.pathname;
  if (!(path === "/health" || path.startsWith("/api/"))) {
    await serveFile(path, response);
    return;
  }
  let raw = "";
  try {
    raw = await readRequest(request);
  } catch (error) {
    const status = error.statusCode || 400;
    response.writeHead(status, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ error: status === 413 ? "request too large" : "invalid request" }));
    return;
  }
  const result = await handler({
    headers: request.headers,
    rawPath: path,
    requestContext: { http: { method: request.method || "GET" } },
    body: raw,
    isBase64Encoded: false,
  });
  response.writeHead(result.statusCode || 500, result.headers || { "Content-Type": "application/json" });
  response.end(result.body || "");
});

server.listen(port, "127.0.0.1", () => {
  console.log(`Lifeline is running at http://127.0.0.1:${port}`);
});
EOF
