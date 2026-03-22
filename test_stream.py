import httpx, json, sys

url = "http://127.0.0.1:4000/v1/chat/completions"

with httpx.Client() as client:
    r1 = client.post(url, json={
        "model": "qwen/qwen3-coder-next",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello."}
        ]
    })
    messages = r1.json()['choices'][0]['message']
    
with httpx.stream("POST", url, json={
    "model": "qwen/qwen3-coder-next",
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Say hello."},
        messages,
        {"role": "user", "content": "Now say bye."}
    ],
    "stream": True
}) as r2:
    for line in r2.iter_lines():
        if line:
            print("LINE:", line)
