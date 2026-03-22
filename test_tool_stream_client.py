from openai import OpenAI
import time

# We are testing through the proxy we just fixed
client = OpenAI(base_url="http://localhost:4000/v1", api_key="lm-studio")
MODEL = "qwen/qwen3-coder-next" # The user's mock model or whatever is loaded

TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "Get the current time, only if asked",
        "parameters": {"type": "object", "properties": {}},
    },
}

def process_stream(stream):
    """Handle streaming responses from the API"""
    collected_text = ""
    tool_calls = []
    
    for chunk in stream:
        delta = chunk.choices[0].delta

        # Handle text output
        if delta.content:
            collected_text += delta.content

        # Handle tool calls
        elif delta.tool_calls:
            for tc in delta.tool_calls:
                if len(tool_calls) <= tc.index:
                    tool_calls.append({
                        "id": "", "type": "function",
                        "function": {"name": "", "arguments": ""}
                    })
                tool_calls[tc.index] = {
                    "id": (tool_calls[tc.index]["id"] + (tc.id or "")),
                    "type": "function",
                    "function": {
                        "name": (tool_calls[tc.index]["function"]["name"] + (tc.function.name or "")),
                        "arguments": (tool_calls[tc.index]["function"]["arguments"] + (tc.function.arguments or ""))
                    }
                }
    return collected_text, tool_calls

messages = [{"role": "user", "content": "Tell me the setup for a joke, then tell me the current time"}]
response_text, tool_calls = process_stream(
    client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=[TIME_TOOL],
        stream=True,
        tool_choice={"type": "function", "function": {"name": "get_current_time"}},
        temperature=0.2
    )
)

print(f"Assistant: {response_text}")
print(f"Tools called: {tool_calls}")
