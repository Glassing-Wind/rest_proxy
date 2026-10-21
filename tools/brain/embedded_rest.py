"""Opt-in bounded REST evidence reads and explicitly configured learning mutations."""
import json

from pydantic import ValidationError
from starlette.responses import JSONResponse, Response

READ_TOOLS = frozenset({
    'assemble_context_bundle',
    'assemble_fire_context_bundle',
    'list_embedded_projects', 'get_embedded_overview', 'describe_embedded_file',
    'get_embedded_file_facts', 'get_embedded_relationships', 'search_embedded_repository',
    'get_repository_learning_profile', 'compare_repository_learning_profiles',
    'get_learned_repository_patterns', 'suggest_learned_repository_patterns',
})
LEARNING_WRITE_TOOLS = frozenset({
    'learn_repository_patterns', 'learn_text_patterns', 'delete_repository_learning',
    'learn_configuration_patterns',
})
MAX_REQUEST_BYTES = 16000
MAX_WRITE_REQUEST_BYTES = 20 * 1024
MAX_RESPONSE_BYTES = 48000


def make_read_endpoint(mcp):
    """Reuse tool validation and shared ownership; never create another runtime."""
    return _make_endpoint(mcp, READ_TOOLS, MAX_REQUEST_BYTES, 'unsupported-read-tool',
                          'embedded-tool-unavailable', 'evidence-read-unavailable')


def make_write_endpoint(mcp):
    """Route only explicit learning mutations; never allow general/admin tool dispatch."""
    return _make_endpoint(mcp, LEARNING_WRITE_TOOLS, MAX_WRITE_REQUEST_BYTES,
                          'unsupported-learning-write-tool', 'learning-tool-unavailable',
                          'learning-write-unavailable')


def _make_endpoint(mcp, allowed_tools, max_request_bytes, unsupported_error, unavailable_error, execution_error):
    async def invoke(request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > max_request_bytes:
                return JSONResponse({'error': 'request-too-large'}, status_code=413)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({'error': 'invalid-json'}, status_code=400)
        if not isinstance(payload, dict) or set(payload) != {'tool', 'arguments'}:
            return JSONResponse({'error': 'expected-tool-and-arguments'}, status_code=422)
        name, arguments = payload['tool'], payload['arguments']
        if not isinstance(name, str) or name not in allowed_tools:
            return JSONResponse({'error': unsupported_error}, status_code=422)
        if not isinstance(arguments, dict):
            return JSONResponse({'error': 'arguments-must-be-object'}, status_code=422)
        if name == 'search_embedded_repository':
            if arguments.get('mode', 'text') != 'text':
                return JSONResponse({'error': 'only-text-search-supported'}, status_code=422)
            arguments['mode'] = 'text'
        tool = mcp._tool_manager.get_tool(name)
        if tool is None:
            return JSONResponse({'error': unavailable_error}, status_code=503)
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
            return JSONResponse({'error': execution_error}, status_code=503)
    return invoke
