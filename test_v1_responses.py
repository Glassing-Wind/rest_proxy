import httpx

url = "http://127.0.0.1:1234/v1/responses"
payload = {
    "model": "qwen/qwen3-coder-next",
    "input": [{"role": "user", "content": "What is 2+2?"}],
    "stream": True,
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {"loc": {"type": "string"}}}
            }
        }
    ]
}

with httpx.stream("POST", url, json=payload, timeout=60) as r:
    print("Status:", r.status_code)
    try:
        err = r.read()
        print("Body:", err)
    except:
        pass
