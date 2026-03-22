import httpx

url = "http://127.0.0.1:1234/v1/responses"
payload = {
    "model": "qwen/qwen3-coder-next",
    "input": [{"role": "system", "content": "you are a bot."}],
    "instructions": "you are a bot."
}

with httpx.Client() as c:
    r = c.post(url, json=payload)
    print(r.status_code, r.text)
