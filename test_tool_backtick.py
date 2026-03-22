import httpx

payload = {
    "model": "qwen/qwen3-coder-next",
    "messages": [
        {"role": "user", "content": "Call the codebase_search tool. You MUST format it as a markdown code block starting with ```json\n{\"name\": \"codebase_search\", \"arguments\": {\"query\": \"DebugAutomationPanel\"}}\n```"}
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "codebase_search",
                "description": "search",
                "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}
            }
        }
    ],
    "stream": True,
}

with httpx.stream("POST", "http://127.0.0.1:1234/v1/chat/completions", json=payload, timeout=60) as r:
    for line in r.iter_lines():
        if line:
            print(line)
