"""Opt-in shared MCP/REST bundle builder; no provider or storage lifecycle changes."""
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from memory.context_bundle import build_context_bundle
from memory.fire_context import adapt_checkpoint
from memory.fire_store import FireStore


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


async def assemble_fire_context_bundle(request: dict, current_hashes: dict | None = None,
                                       expected_revision: int | None = None,
                                       max_originals: int = 5, max_chars: int = 4000) -> dict:
    """Pack one scoped FIRE snapshot as historical evidence, retaining the caller's goal.

    Does not promote recovered constraints/decisions to instructions or revalidate
    live source. Missing/expired state yields explicit degraded caller-only context.
    Checkpoint and original candidates follow history deduplication/continuation policy.
    """
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
        return {'status': 'invalid-revision', 'recovered_or_injected': False}
    if (type(max_originals) is not int or not 0 <= max_originals <= 10
            or type(max_chars) is not int or not 1 <= max_chars <= 8000):
        return {'status': 'invalid-fire-excerpt-limits', 'recovered_or_injected': False}
    try:
        request = json.loads(json.dumps(request, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError):
        return {'status': 'invalid-context-request-or-tokenizer', 'recovered_or_injected': False}
    base_bundle = await assemble_context_bundle(request)
    if 'payload' not in base_bundle:
        return base_bundle
    recovery = {'status': 'disabled'}
    adapted = request
    state = os.getenv('LM_PROXY_FIRE_STATE', '').strip()
    try:
        if state:
            snapshot = await asyncio.to_thread(lambda: FireStore(state).resume(
                request['project_id'], request['session_id'], request['task_id'],
                current_hashes=current_hashes, _include_originals=True))
            recovery = {key: snapshot[key] for key in ('status', 'revision') if key in snapshot}
            if snapshot['status'] == 'resumed':
                if expected_revision is not None and snapshot['revision'] != expected_revision:
                    recovery['status'] = 'revision-conflict'
                elif snapshot['expires_at'] <= time.time():
                    recovery['status'] = 'expired'
                else:
                    try:
                        adapted, recovery = adapt_checkpoint(request, snapshot,
                            max_originals=max_originals, max_chars=max_chars)
                    except ValueError:
                        recovery = {'status': 'snapshot-does-not-fit-contract', 'revision': snapshot['revision']}
    except (ValueError, TypeError, KeyError):
        return {'status': 'invalid-fire-context-request-or-state', 'recovered_or_injected': False}
    except Exception:
        recovery = {'status': 'storage-unavailable'}
    bundle = await assemble_context_bundle(adapted) if adapted is not request else base_bundle
    # A recovered snapshot may exceed input/resource limits; preserve usable caller context.
    if 'payload' not in bundle and adapted is not request:
        bundle = base_bundle
        recovery = {'status': 'snapshot-does-not-fit-contract', 'revision': recovery.get('revision')}
    bundle['fire_recovery'] = recovery
    if 'omissions' in bundle:
        if recovery['status'] != 'snapshot-adapted':
            bundle['omissions'].append({'id': 'fire-checkpoint', 'reason': 'fire-' + recovery['status']})
        else:
            bundle['omissions'].extend(row for row in recovery['diagnostics'] if row['reason'] != 'fire-original-excerpted')
        bundle['omissions'].sort(key=lambda row: (row['id'], row['reason']))
    if len(json.dumps(bundle, ensure_ascii=False).encode()) > 48000:
        return {'status': 'fire-bundle-response-too-large', 'recovered_or_injected': False}
    return bundle


def register(mcp):
    if os.getenv('LM_PROXY_CONTEXT_ENABLED', '0').strip().lower() in {'1', 'true', 'yes', 'on'}:
        mcp.tool()(assemble_context_bundle)
        if os.getenv('LM_PROXY_FIRE_STATE', '').strip():
            mcp.tool()(assemble_fire_context_bundle)
