#!/usr/bin/env python3
"""Smoke-check the local Streamable HTTP MCP endpoint."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


BASE_URL = os.environ.get("BRAIN_SERVER_BASE_URL") or f"http://127.0.0.1:{os.environ.get('BRAIN_SERVER_PORT', '8001')}"
MCP_URL = f"{BASE_URL}/mcp"
HEALTH_URL = f"{BASE_URL}/health"
PROTOCOL_VERSION = "2025-06-18"


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
) -> tuple[int, dict[str, str], str]:
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
        req_headers.setdefault("Accept", "application/json, text/event-stream")

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, raw


def _extract_sse_json(raw: str) -> dict:
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("No SSE data frame found in initialize response")


def _wait_for_server(timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, _, _ = _request(HEALTH_URL)
            if status == 200:
                return
        except Exception as exc:  # pragma: no cover - defensive
            last_error = exc
        time.sleep(0.25)
    if last_error:
        raise AssertionError(f"MCP server did not become ready: {last_error}")
    raise AssertionError("MCP server did not become ready")


def main() -> int:
    _wait_for_server()

    initialize_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "graphrag-smoke", "version": "1.0"},
        },
    }

    status, headers, body = _request(
        MCP_URL,
        method="POST",
        headers={"MCP-Protocol-Version": PROTOCOL_VERSION},
        body=initialize_payload,
    )
    assert status == 200, f"initialize failed with status={status}"
    assert headers.get("content-type") == "text/event-stream", headers
    session_id = headers.get("mcp-session-id")
    assert session_id, "initialize response did not include Mcp-Session-Id"
    initialize_msg = _extract_sse_json(body)
    result = initialize_msg.get("result") or {}
    assert result.get("protocolVersion") == PROTOCOL_VERSION, result

    health_status, health_headers, health_body = _request(HEALTH_URL)
    assert health_status == 200, f"health failed with status={health_status}"
    health = json.loads(health_body)
    assert health.get("standard") == f"Streamable HTTP ({PROTOCOL_VERSION})", health
    assert health_headers.get("x-graphrag-boot-id"), health_headers
    assert health_headers.get("x-graphrag-tool-fingerprint"), health_headers

    delete_status, delete_headers, _ = _request(
        MCP_URL,
        method="DELETE",
        headers={
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Session-Id": session_id,
        },
    )
    assert delete_status == 200, f"DELETE failed with status={delete_status}"
    assert delete_headers.get("mcp-session-id"), delete_headers

    fallback_payload = {
        **initialize_payload,
        "id": 2,
        "params": {
            **initialize_payload["params"],
            "clientInfo": {"name": "graphrag-headerless-smoke", "version": "1.0"},
        },
    }
    fallback_status, fallback_headers, fallback_body = _request(
        MCP_URL,
        method="POST",
        body=fallback_payload,
    )
    assert fallback_status == 200, f"headerless initialize failed with status={fallback_status}"
    fallback_session_id = fallback_headers.get("mcp-session-id")
    assert fallback_session_id, "headerless initialize did not include Mcp-Session-Id"
    fallback_result = (_extract_sse_json(fallback_body).get("result") or {})
    assert fallback_result.get("protocolVersion") == PROTOCOL_VERSION, fallback_result

    fallback_delete_status, _, _ = _request(
        MCP_URL,
        method="DELETE",
        headers={
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Session-Id": fallback_session_id,
        },
    )
    assert fallback_delete_status == 200, (
        f"headerless session DELETE failed with status={fallback_delete_status}"
    )

    print("MCP protocol smoke check passed")
    print(f"- initialize protocol version: {PROTOCOL_VERSION}")
    print(f"- session id returned: {session_id}")
    print(f"- headerless initialize negotiated: {fallback_result['protocolVersion']}")
    print(f"- health standard: {health['standard']}")
    print(f"- boot id: {health['boot_id']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"MCP protocol smoke check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
