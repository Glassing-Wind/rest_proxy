"""Opt-in stateless LM Studio forwarding; evidence remains owned by the HTTP daemon."""
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from memory.context_bundle import canonical


def loopback(url, path):
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}
            or parsed.port is None or parsed.path != path or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError('Require a loopback HTTP endpoint with explicit port and expected path')
    return url


def format_chat(bundle, model):
    if bundle.get('status') not in {'assembled', 'already-present'} or not bundle.get('accounting', {}).get('fits_accounted_budget'):
        raise ValueError('Bundle does not fit the declared request budget')
    payload = bundle['payload']
    messages = []
    if payload['instructions']:
        messages.append({'role': 'system', 'content': payload['instructions']})
    messages.append({'role': 'user', 'content': 'Current task goal: ' + payload['goal']})
    messages.extend(payload['messages'])
    reserve = bundle['accounting']['output_reserve']
    if type(reserve) is not int or not 1 <= reserve <= 4096:
        raise ValueError('This forwarding slice requires output reserve 1..4096')
    body = {'model': model, 'messages': messages, 'max_tokens': reserve,
            'stream': False, 'temperature': 0, 'reasoning_effort': 'none'}
    if payload['tools']:
        if any(tool.get('type') != 'function' or not isinstance(tool.get('function'), dict)
               or not tool['function'].get('name') for tool in payload['tools']):
            raise ValueError('Forwarded tools require OpenAI function schemas')
        body['tools'] = payload['tools']
    raw = canonical(body).encode()
    artifact = os.getenv('LM_PROXY_CONTEXT_TOKENIZER_FILE', '').strip()
    method = 'utf8-byte-estimate'
    if artifact:
        from tokenizers import Tokenizer
        path = Path(artifact).expanduser().resolve()
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Tokenizer exceeds 32 MiB')
        tokenizer_raw = path.read_bytes()
        if len(tokenizer_raw) > 32 * 1024 * 1024:
            raise ValueError('Tokenizer exceeds 32 MiB')
        tokenizer = Tokenizer.from_str(tokenizer_raw.decode())
        tokenizer.no_truncation()
        tokenizer.no_padding()
        counted = len(tokenizer.encode(raw.decode()).ids)
        method = 'local-tokenizer-json-sha256:' + hashlib.sha256(tokenizer_raw).hexdigest()
    else:
        counted = len(raw)
    account = bundle['accounting']
    total = counted + reserve + account['framing_reserve'] + account['safety_margin']
    if total > account['context_capacity']:
        raise ValueError('Formatted provider request exceeds declared budget')
    return body, {'method': method, 'serialized_input': counted, 'total_reserved': total,
                  'context_capacity': account['context_capacity'], 'chat_template_verified': False}


async def context_chat(request: Request):
    from proxy.config import INFERENCE_PROVIDER, LM_BASE
    if INFERENCE_PROVIDER != 'lmstudio':
        return JSONResponse({'error': 'context-route-requires-current-lmstudio-provider'}, status_code=503)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16000:
            return JSONResponse({'error': 'request-too-large'}, status_code=413)
    try:
        incoming = json.loads(raw)
        allowed = {'model', 'context_request', 'fire', 'expected_revision', 'current_hashes', 'max_originals', 'max_chars'}
        if not isinstance(incoming, dict) or set(incoming) - allowed:
            raise ValueError('Unknown forwarding fields')
        model = incoming['model']
        context_request = incoming['context_request']
        if not isinstance(model, str) or not model or len(model) > 256 or not isinstance(context_request, dict):
            raise ValueError('Require model and context request')
        if context_request.get('provider_continuation') or context_request.get('provider_context_tokens'):
            raise ValueError('This route supports stateless requests only')
        if type(incoming.get('fire', False)) is not bool:
            raise ValueError('FIRE selection must be boolean')
        owner_url = loopback(os.getenv('LM_PROXY_CONTEXT_OWNER_URL', 'http://127.0.0.1:8001/evidence/read'), '/evidence/read')
        model_url = loopback(LM_BASE, '')
        arguments = {'request': context_request}
        if incoming.get('fire'):
            arguments.update({key: incoming[key] for key in ('expected_revision', 'current_hashes', 'max_originals', 'max_chars') if key in incoming})
        elif set(incoming) & {'expected_revision', 'current_hashes', 'max_originals', 'max_chars'}:
            raise ValueError('Recovery options require FIRE selection')
        headers = {}
        if os.getenv('LMSTUDIO_API_KEY', '').strip():
            headers['Authorization'] = 'Bearer ' + os.environ['LMSTUDIO_API_KEY'].strip()
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5)) as client:
            assembled = await client.post(owner_url, json={'tool': 'assemble_fire_context_bundle' if incoming.get('fire') else 'assemble_context_bundle', 'arguments': arguments})
            assembled.raise_for_status()
            bundle = assembled.json()
            if incoming.get('fire'):
                recovery = bundle.get('fire_recovery', {})
                if recovery.get('status') != 'snapshot-adapted' or recovery.get('expires_at', 0) <= time.time():
                    return JSONResponse({'error': 'required-fire-snapshot-unavailable-or-expired'}, status_code=409)
            body, accounting = format_chat(bundle, model)
            preflight = await client.get(model_url + '/api/v1/models', headers=headers)
            preflight.raise_for_status()
            instances = [instance for item in preflight.json().get('models', [])
                         if item.get('type') == 'llm' and item.get('key') == model
                         for instance in item.get('loaded_instances', []) if instance.get('id') == model]
            if len(instances) != 1:
                return JSONResponse({'error': 'model-not-uniquely-loaded'}, status_code=409)
            loaded_capacity = instances[0].get('config', {}).get('context_length')
            if type(loaded_capacity) is not int or accounting['context_capacity'] > loaded_capacity:
                return JSONResponse({'error': 'declared-capacity-exceeds-or-lacks-loaded-model-capacity'}, status_code=409)
            response = await client.post(model_url + '/v1/chat/completions', content=canonical(body),
                                         headers=dict(headers, **{'Content-Type': 'application/json'}))
            response.raise_for_status()
            completion = response.json()
        usage = completion.get('usage', {})
        prompt, output = usage.get('prompt_tokens'), usage.get('completion_tokens')
        known = type(prompt) is int and prompt >= 0 and type(output) is int and output >= 0
        completion['x_context'] = {'bundle_id': bundle['bundle_id'], 'selected_ids': [item['id'] for item in bundle['selected']],
            'omissions': bundle['omissions'], 'fire_recovery': bundle.get('fire_recovery'), 'formatting': accounting,
            'actual_usage': usage, 'observed_usage_within_declared_capacity':
                prompt + output <= accounting['context_capacity'] if known else None,
            'observed_prompt_plus_output_reserve_fits':
                prompt + body['max_tokens'] <= accounting['context_capacity'] if known else None,
            'limitations': ['Non-streaming/stateless only; no automatic tool execution or legacy memory injection.',
                            'Usage is observed after inference, not a preflight chat-template attestation.']}
        return JSONResponse(completion)
    except (ValueError, TypeError, KeyError):
        return JSONResponse({'error': 'invalid-context-forwarding-request-or-budget'}, status_code=422)
    except Exception:
        return JSONResponse({'error': 'context-owner-or-provider-unavailable'}, status_code=502)
