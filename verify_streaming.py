import httpx
import json

def test_multi_turn():
    url = "http://127.0.0.1:4000/v1/chat/completions"
    
    # Turn 1: Initiating State
    print("\n--- Turn 1 (Initiating State) ---")
    payload1 = {
        "model": "qwen/qwen3-coder-next",
        "messages": [{"role": "user", "content": "Hi there! Remember the number 42."}],
        "stream": False
    }
    
    with httpx.Client(timeout=60) as client:
        r1 = client.post(url, json=payload1)
        r1.raise_for_status()
        print(f"Assistant: {r1.json()['choices'][0]['message']['content']}")

    # Turn 2: Stateful Continuation
    print("\n--- Turn 2 (Stateful) ---")
    payload2 = {
        "model": "qwen/qwen3-coder-next",
        "messages": [
            {"role": "user", "content": "Hi there! Remember the number 42."},
            {"role": "assistant", "content": r1.json()['choices'][0]['message']['content']},
            {"role": "user", "content": "What number did I tell you to remember?"}
        ],
        "stream": True
    }
    
    with httpx.stream("POST", url, json=payload2, timeout=60) as r2:
        r2.raise_for_status()
        for line in r2.iter_lines():
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    print("\n[DONE]")
                    break
                data = json.loads(data_str)
                content = data['choices'][0]['delta'].get('content', '')
                print(content, end="", flush=True)

if __name__ == "__main__":
    test_multi_turn()
