import requests
import json

url = "http://127.0.0.1:1234/v1/responses"

payloads = [
    {"name": "Empty input array", "payload": {"model": "qwen/qwen3-coder-next", "input": []}},
    {"name": "System only in input", "payload": {"model": "qwen/qwen3-coder-next", "input": [{"role": "system", "content": "hello"}]}},
    {"name": "Content is null", "payload": {"model": "qwen/qwen3-coder-next", "input": [{"role": "user", "content": None}]}},
    {"name": "Missing content", "payload": {"model": "qwen/qwen3-coder-next", "input": [{"role": "user"}]}},
]

for p in payloads:
    r = requests.post(url, json=p['payload'])
    print(f"--- {p['name']} ---")
    print(r.status_code, r.text)
    
