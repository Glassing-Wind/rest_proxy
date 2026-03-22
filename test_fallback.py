import httpx
import json

url = "http://127.0.0.1:4000/v1/chat/completions"

# Empty input array triggers 400 invalid_union on /v1/responses.
# The proxy should catch the 400 and fall back to /v1/chat/completions.
payload = {
    "model": "qwen/qwen3-coder-next",
    "messages": [], # We will simulate this by sending an empty messages array, wait proxy.py line 1477 uses messages. If messages is [], then payload_input is []
    "stream": True
}

with httpx.stream("POST", url, json=payload, timeout=60) as r:
    print("Status:", r.status_code)
    for line in r.iter_lines():
        if line:
            print(line)
