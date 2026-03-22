import httpx

payload = {
    "model": "qwen/qwen3-coder-next",
    "messages": [
        {"role": "user", "content": "Write a Hello World in python using a markdown code block."}
    ],
    "stream": True,
}

with httpx.stream("POST", "http://127.0.0.1:1234/v1/chat/completions", json=payload, timeout=60) as r:
    for line in r.iter_lines():
        if line:
            print(line)
