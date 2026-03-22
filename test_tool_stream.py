import httpx
import json

url = "http://127.0.0.1:1234/v1/responses"

# We'll simulate a tool to see what LM Studio returns
payload = {
    "model": "qwen/qwen3-coder-next",
    "input": [{"role": "user", "content": "What is the weather in San Francisco today?"}],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_current_weather",
                "description": "Get the current weather",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "The city and state, e.g. San Francisco, CA"}
                    },
                    "required": ["location"]
                }
            }
        }
    ],
    "stream": True
}

with httpx.stream("POST", url, json=payload, timeout=60) as r:
    for line in r.iter_lines():
        if line:
            print(line)
