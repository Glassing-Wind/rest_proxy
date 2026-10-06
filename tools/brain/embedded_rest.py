"""Opt-in bounded REST reads through the daemon's registered MCP tools."""
import json

from pydantic import ValidationError
from starlette.responses import JSONResponse, Response

READ_TOOLS = frozenset({
    'assemble_context_bundle',
    'list_embedded_projects', 'get_embedded_overview', 'describe_embedded_file',
    'get_embedded_file_facts', 'get_embedded_relationships', 'search_embedded_repository',
})
MAX_REQUEST_BYTES = 16000
MAX_RESPONSE_BYTES = 48000


def make_read_endpoint(mcp):
    """Reuse tool validation and shared ownership; never create another runtime."""
    async def read(request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_REQUEST_BYTES:
                return JSONResponse({'error': 'request-too-large'}, status_code=413)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({'error': 'invalid-json'}, status_code=400)
        if not isinstance(payload, dict) or set(payload) != {'tool', 'arguments'}:
            return JSONResponse({'error': 'expected-tool-and-arguments'}, status_code=422)
        name, arguments = payload['tool'], payload['arguments']
        if not isinstance(name, str) or name not in READ_TOOLS:
            return JSONResponse({'error': 'unsupported-read-tool'}, status_code=422)
        if not isinstance(arguments, dict):
            return JSONResponse({'error': 'arguments-must-be-object'}, status_code=422)
        if name == 'search_embedded_repository':
            if arguments.get('mode', 'text') != 'text':
                return JSONResponse({'error': 'only-text-search-supported'}, status_code=422)
            arguments['mode'] = 'text'
        tool = mcp._tool_manager.get_tool(name)
        if tool is None:
            return JSONResponse({'error': 'embedded-tool-unavailable'}, status_code=503)
        if set(arguments) - set(tool.parameters.get('properties', {})):
            return JSONResponse({'error': 'unknown-tool-arguments'}, status_code=422)
        try:
            tool.fn_metadata.arg_model.model_validate(arguments)
            result = await tool.run(arguments)
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
            if len(encoded) > MAX_RESPONSE_BYTES:
                return JSONResponse({'error': 'response-too-large', 'hint': 'reduce-tool-limits'}, status_code=413)
            return Response(encoded, media_type='application/json')
        except (ValidationError, ValueError):
            return JSONResponse({'error': 'invalid-tool-arguments-or-limits'}, status_code=422)
        except Exception:
            return JSONResponse({'error': 'evidence-read-unavailable'}, status_code=503)
    return read
