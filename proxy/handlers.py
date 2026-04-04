"""proxy/handlers.py — request handling helpers for proxy endpoints."""

from proxy.handlers_utils import (
    is_insufficient_resource_error_text,
    history_key,
    content_to_text,
    _truncate_text,
    _detect_and_break_tool_loop,
    stats_to_usage,
    extract_system_prompt,
    get_last_non_system_message,
    extract_tool_calls_from_api_chat_output,
    extract_text_from_api_chat_output,
    build_chat_completion_response,
)
from proxy.handlers_memory import (
    _derive_session_id,
    _persist_memory_best_effort,
    _inject_memory_into_messages,
)
from proxy.handlers_filtering import (
    request_uses_openai_tools,
    _compact_tool_definitions,
    compact_message_content,
    filter_messages_for_proxy,
    normalize_input_content,
)
from proxy.handlers_streaming import stream_openai_compatible_response
from proxy.handlers_responses import (
    forward_responses_api_completion,
)
from proxy.handlers_chat import forward_openai_chat_completion

__all__ = [
    "is_insufficient_resource_error_text",
    "history_key",
    "_derive_session_id",
    "_persist_memory_best_effort",
    "_inject_memory_into_messages",
    "request_uses_openai_tools",
    "content_to_text",
    "_truncate_text",
    "_detect_and_break_tool_loop",
    "_compact_tool_definitions",
    "compact_message_content",
    "filter_messages_for_proxy",
    "normalize_input_content",
    "extract_tool_calls_from_api_chat_output",
    "stats_to_usage",
    "extract_system_prompt",
    "get_last_non_system_message",
    "extract_text_from_api_chat_output",
    "build_chat_completion_response",
    "stream_openai_compatible_response",
    "forward_responses_api_completion",
    "forward_openai_chat_completion",
]
