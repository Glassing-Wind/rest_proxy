import httpx
import json

url = "http://127.0.0.1:1234/api/v1/chat"

payload = {
    "model": "qwen/qwen3-coder-next",
    "input": [
        {"role": "user", "content": "Tell me the setup for a joke, then tell me the current weather in SF"}
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {"loc": {"type": "string"}}}
            }
        }
    ],
    "stream": True
}

with httpx.stream("POST", url, json=payload, timeout=60) as r:
    print("Status:", r.status_code)
    for line in r.iter_lines():
        if line:
            print(line)
