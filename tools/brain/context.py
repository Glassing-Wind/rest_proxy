"""Opt-in shared MCP/REST bundle builder; no provider or storage lifecycle changes."""
import asyncio
import hashlib
import os
from pathlib import Path

from memory.context_bundle import build_context_bundle


async def assemble_context_bundle(request: dict) -> dict:
    """Pack the complete supplied text request with cited evidence and explicit omissions.

    Requires project/session/task, goal, context_capacity and output_reserve. Local
    tokenizer JSON is optional; fallback is a labeled UTF-8 byte estimate. Provider
    hidden context/framing and current source hashes are caller responsibilities.
    Returns a provider-neutral payload; does not forward it to inference.
    """
    def run():
        artifact = os.getenv('LM_PROXY_CONTEXT_TOKENIZER_FILE', '').strip()
        if not artifact:
            return build_context_bundle(request)
        from tokenizers import Tokenizer
        path = Path(artifact).expanduser().resolve()
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Local tokenizer exceeds 32 MiB')
        raw = path.read_bytes()
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError('Local tokenizer exceeds 32 MiB')
        identity = hashlib.sha256(raw).hexdigest()
        tokenizer = Tokenizer.from_str(raw.decode())
        tokenizer.no_truncation()
        tokenizer.no_padding()
        return build_context_bundle(request, tokenizer, 'local-tokenizer-json-sha256:' + identity)
    try:
        return await asyncio.to_thread(run)
    except (ValueError, TypeError, KeyError):
        return {'status': 'invalid-context-request-or-tokenizer', 'recovered_or_injected': False}
    except Exception:
        return {'status': 'context-builder-unavailable', 'recovered_or_injected': False}


def register(mcp):
    if os.getenv('LM_PROXY_CONTEXT_ENABLED', '0').strip().lower() in {'1', 'true', 'yes', 'on'}:
        mcp.tool()(assemble_context_bundle)
