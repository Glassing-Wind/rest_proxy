
LM Studio offers a powerful REST API with first-class support for local inference and model management. In addition to our native API, we provide OpenAI-compatible endpoints ([learn more](/docs/developer/openai-compat)) and Anthropic-compatible endpoints ([learn more](/docs/developer/anthropic-compat)).

## What's new
Previously, there was a [v0 REST API](/docs/developer/rest/endpoints). With LM Studio 0.4.0, we have officially released our native v1 REST API at `/api/v1/*` endpoints and recommend using it.

The v1 REST API includes enhanced features such as:
- [MCP via API](/docs/developer/core/mcp)
- [Stateful chats](/docs/developer/rest/stateful-chats)
- [Authentication](/docs/developer/core/authentication) configuration with API tokens
- Model [download](/docs/developer/rest/download), [load](/docs/developer/rest/load) and [unload](/docs/developer/rest/unload) endpoints

## Supported endpoints
The following endpoints are available in LM Studio's v1 REST API.
<table class="flexible-cols">
  <thead>
    <tr>
      <th>Endpoint</th>
      <th>Method</th>
      <th>Docs</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><code>/api/v1/chat</code></td>
      <td><apimethod method="POST" /></td>
      <td><a href="/docs/developer/rest/chat">Chat</a></td>
    </tr>
    <tr>
      <td><code>/api/v1/models</code></td>
      <td><apimethod method="GET" /></td>
      <td><a href="/docs/developer/rest/list">List Models</a></td>
    </tr>
    <tr>
      <td><code>/api/v1/models/load</code></td>
      <td><apimethod method="POST" /></td>
      <td><a href="/docs/developer/rest/load">Load</a></td>
    </tr>
    <tr>
        <td><code>/api/v1/models/unload</code></td>
        <td><apimethod method="POST" /></td>
        <td><a href="/docs/developer/rest/unload">Unload</a></td>
    </tr>
    <tr>
      <td><code>/api/v1/models/download</code></td>
      <td><apimethod method="POST" /></td>
      <td><a href="/docs/developer/rest/download">Download</a></td>
    </tr>
    <tr>
      <td><code>/api/v1/models/download/status</code></td>
      <td><apimethod method="GET" /></td>
      <td><a href="/docs/developer/rest/download-status">Download Status</a></td>
    </tr>
  </tbody>
</table>

## Inference endpoint comparison
The table below compares the features of LM Studio's `/api/v1/chat` endpoint with OpenAI-compatible and Anthropic-compatible inference endpoints.
<table class="flexible-cols">
  <thead>
    <tr>
      <th>Feature</th>
      <th><a href="/docs/developer/rest/chat"><code>/api/v1/chat</code></a></th>
      <th><a href="/docs/developer/openai-compat/responses"><code>/v1/responses</code></a></th>
      <th><a href="/docs/developer/openai-compat/chat-completions"><code>/v1/chat/completions</code></a></th>
      <th><a href="/docs/developer/anthropic-compat/messages"><code>/v1/messages</code></a></th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Streaming</td>
      <td>✅</td>
      <td>✅</td>
      <td>✅</td>
      <td>✅</td>
    </tr>
    <tr>
      <td>Stateful chat</td>
      <td>✅</td>
      <td>✅</td>
      <td>❌</td>
      <td>❌</td>
    </tr>
    <tr>
      <td>Remote MCPs</td>
      <td>✅</td>
      <td>✅</td>
      <td>❌</td>
      <td>❌</td>
    </tr>
    <tr>
      <td>MCPs you have in LM Studio</td>
      <td>✅</td>
      <td>✅</td>
      <td>❌</td>
      <td>❌</td>
    </tr>
    <tr>
      <td>Custom tools</td>
      <td>❌</td>
      <td>✅</td>
      <td>✅</td>
      <td>✅</td>
    </tr>
    <tr>
      <td>Include assistant messages in the request</td>
      <td>❌</td>
      <td>✅</td>
      <td>✅</td>
      <td>✅</td>
    </tr>
    <tr>
      <td>Model load streaming events</td>
      <td>✅</td>
      <td>❌</td>
      <td>❌</td>
      <td>❌</td>
    </tr>
    <tr>
      <td>Prompt processing streaming events</td>
      <td>✅</td>
      <td>❌</td>
      <td>❌</td>
      <td>❌</td>
    </tr>
    <tr>
      <td>Specify context length in the request</td>
      <td>✅</td>
      <td>❌</td>
      <td>❌</td>
      <td>❌</td>
    </tr>
  </tbody>
</table>

---

Please report bugs by opening an issue on [Github](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues).


## Start the server

[Install](/download) and launch LM Studio.

Then ensure the server is running through the toggle at the top left of the Developer page, or through [lms](/docs/cli) in the terminal:

```bash
lms server start
```

By default, the server is available at `http://localhost:1234`.

If you don't have a model downloaded yet, you can download the model:

```bash
lms get ibm/granite-4-micro
```


## API Authentication

By default, the LM Studio API server does **not** require authentication. You can configure the server to require authentication by API token in the [server settings](/docs/developer/core/server/settings) for added security.

To authenticate API requests, generate an API token from the Developer page in LM Studio, and include it in the `Authorization` header of your requests as follows: `Authorization: Bearer $LM_API_TOKEN`. Read more about authentication [here](/docs/developer/core/authentication).


## Chat with a model

Use the chat endpoint to send a message to a model. By default, the model will be automatically loaded if it is not already.

The `/api/v1/chat` endpoint is stateful, which means you do not need to pass the full history in every request. Read more about it [here](/docs/developer/rest/stateful-chats).

```lms_code_snippet
variants:
  curl:
    language: bash
    code: |
      curl http://localhost:1234/api/v1/chat \
        -H "Authorization: Bearer $LM_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "ibm/granite-4-micro",
          "input": "Write a short haiku about sunrise."
        }'
  Python:
    language: python
    code: |
      import os
      import requests
      import json

      response = requests.post(
        "http://localhost:1234/api/v1/chat",
        headers={
          "Authorization": f"Bearer {os.environ['LM_API_TOKEN']}",
          "Content-Type": "application/json"
        },
        json={
          "model": "ibm/granite-4-micro",
          "input": "Write a short haiku about sunrise."
        }
      )
      print(json.dumps(response.json(), indent=2))
  TypeScript:
    language: typescript
    code: |
      const response = await fetch("http://localhost:1234/api/v1/chat", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${process.env.LM_API_TOKEN}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          model: "ibm/granite-4-micro",
          input: "Write a short haiku about sunrise."
        })
      });
      const data = await response.json();
      console.log(data);
```

See the full [chat](/docs/developer/rest/chat) docs for more details.

## Use MCP servers via API


Enable the model interact with ephemeral Model Context Protocol (MCP) servers in `/api/v1/chat` by specifying servers in the `integrations` field.

```lms_code_snippet
variants:
  curl:
    language: bash
    code: |
      curl http://localhost:1234/api/v1/chat \
        -H "Authorization: Bearer $LM_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "ibm/granite-4-micro",
          "input": "What is the top trending model on hugging face?",
          "integrations": [
            {
              "type": "ephemeral_mcp",
              "server_label": "huggingface",
              "server_url": "https://huggingface.co/mcp",
              "allowed_tools": ["model_search"]
            }
          ],
          "context_length": 8000
        }'
  Python:
    language: python
    code: |
      import os
      import requests
      import json

      response = requests.post(
        "http://localhost:1234/api/v1/chat",
        headers={
          "Authorization": f"Bearer {os.environ['LM_API_TOKEN']}",
          "Content-Type": "application/json"
        },
        json={
          "model": "ibm/granite-4-micro",
          "input": "What is the top trending model on hugging face?",
          "integrations": [
            {
              "type": "ephemeral_mcp",
              "server_label": "huggingface",
              "server_url": "https://huggingface.co/mcp",
              "allowed_tools": ["model_search"]
            }
          ],
          "context_length": 8000
        }
      )
      print(json.dumps(response.json(), indent=2))
  TypeScript:
    language: typescript
    code: |
      const response = await fetch("http://localhost:1234/api/v1/chat", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${process.env.LM_API_TOKEN}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          model: "ibm/granite-4-micro",
          input: "What is the top trending model on hugging face?",
          integrations: [
            {
              type: "ephemeral_mcp",
              server_label: "huggingface",
              server_url: "https://huggingface.co/mcp",
              allowed_tools: ["model_search"]
            }
          ],
          context_length: 8000
        })
      const data = await response.json();
      console.log(data);
```

You can also use locally configured MCP plugins (from your `mcp.json`) via the `integrations` field. Using locally run MCP plugins requires authentication via an API token passed through the `Authorization` header. Read more about authentication [here](/docs/developer/core/authentication).

```lms_code_snippet
variants:
  curl:
    language: bash
    code: |
      curl http://localhost:1234/api/v1/chat \
        -H "Authorization: Bearer $LM_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "ibm/granite-4-micro",
          "input": "Open lmstudio.ai",
          "integrations": [
            {
              "type": "plugin",
              "id": "mcp/playwright",
              "allowed_tools": ["browser_navigate"]
            }
          ],
          "context_length": 8000
        }'
  Python:
    language: python
    code: |
      import os
      import requests
      import json

      response = requests.post(
        "http://localhost:1234/api/v1/chat",
        headers={
          "Authorization": f"Bearer {os.environ['LM_API_TOKEN']}",
          "Content-Type": "application/json"
        },
        json={
          "model": "ibm/granite-4-micro",
          "input": "Open lmstudio.ai",
          "integrations": [
            {
              "type": "plugin",
              "id": "mcp/playwright",
              "allowed_tools": ["browser_navigate"]
            }
          ],
          "context_length": 8000
        }
      )
      print(json.dumps(response.json(), indent=2))
  TypeScript:
    language: typescript
    code: |
      const response = await fetch("http://localhost:1234/api/v1/chat", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${process.env.LM_API_TOKEN}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          model: "ibm/granite-4-micro",
          input: "Open lmstudio.ai",
          integrations: [
            {
              type: "plugin",
              id: "mcp/playwright",
              allowed_tools: ["browser_navigate"]
            }
          ],
          context_length: 8000
        })
      });
      const data = await response.json();
      console.log(data);
```

See the full [chat](/docs/developer/rest/chat) docs for more details.

## Download a model

Use the download endpoint to download models by identifier from the [LM Studio model catalog](https://lmstudio.ai/models), or by Hugging Face model URL.

```lms_code_snippet
variants:
  curl:
    language: bash
    code: |
      curl http://localhost:1234/api/v1/models/download \
        -H "Authorization: Bearer $LM_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "ibm/granite-4-micro"
        }'
  Python:
    language: python
    code: |
      import os
      import requests
      import json

      response = requests.post(
        "http://localhost:1234/api/v1/models/download",
        headers={
          "Authorization": f"Bearer {os.environ['LM_API_TOKEN']}",
          "Content-Type": "application/json"
        },
        json={"model": "ibm/granite-4-micro"}
      )
      print(json.dumps(response.json(), indent=2))
  TypeScript:
    language: typescript
    code: |
      const response = await fetch("http://localhost:1234/api/v1/models/download", {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${process.env.LM_API_TOKEN}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          model: "ibm/granite-4-micro"
        })
      });
      const data = await response.json();
      console.log(data);
```

The response will return a `job_id` that you can use to track download progress.

```lms_code_snippet
variants:
  curl:
    language: bash
    code: |
      curl -H "Authorization: Bearer $LM_API_TOKEN" \
        http://localhost:1234/api/v1/models/download/status/{job_id}
  Python:
    language: python
    code: |
      import os
      import requests
      import json

      job_id = "your-job-id"
      response = requests.get(
        f"http://localhost:1234/api/v1/models/download/status/{job_id}",
        headers={"Authorization": f"Bearer {os.environ['LM_API_TOKEN']}"}
      )
      print(json.dumps(response.json(), indent=2))
  TypeScript:
    language: typescript
    code: |
      const jobId = "your-job-id";
      const response = await fetch(
        `http://localhost:1234/api/v1/models/download/status/${jobId}`,
        {
          headers: {
            "Authorization": `Bearer ${process.env.LM_API_TOKEN}`
          }
        }
      );
      const data = await response.json();
      console.log(data);
```

See the [download](/docs/developer/rest/download) and [download status](/docs/developer/rest/download-status) docs for more details.


The `/api/v1/chat` endpoint is stateful by default. This means you don't need to pass the full conversation history in every request — LM Studio automatically stores and manages the context for you.

## How it works

When you send a chat request, LM Studio stores the conversation in a chat thread and returns a `response_id` in the response. Use this `response_id` in subsequent requests to continue the conversation.

```lms_code_snippet
title: Start a new conversation
variants:
  curl:
    language: bash
    code: |
      curl http://localhost:1234/api/v1/chat \
        -H "Authorization: Bearer $LM_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "ibm/granite-4-micro",
          "input": "My favorite color is blue."
        }'
```

The response includes a `response_id`:

```lms_info
Every response includes an unique `response_id` that you can use to reference that specific point in the conversation for future requests. This allows you to branch conversations.
```

```lms_code_snippet
title: Response
variants:
  response:
    language: json
    code: |
      {
        "model_instance_id": "ibm/granite-4-micro",
        "output": [
          {
            "type": "message",
            "content": "That's great! Blue is a beautiful color..."
          }
        ],
        "response_id": "resp_abc123xyz..."
      }
```

## Continue a conversation

Pass the `previous_response_id` in your next request to continue the conversation. The model will remember the previous context.



```lms_code_snippet
title: Continue the conversation
variants:
  curl:
    language: bash
    code: |
      curl http://localhost:1234/api/v1/chat \
        -H "Authorization: Bearer $LM_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "ibm/granite-4-micro",
          "input": "What color did I just mention?",
          "previous_response_id": "resp_abc123xyz..."
        }'
```

The model can reference the previous message without you needing to resend it and will return a new `response_id` for further continuation.

## Disable stateful storage

If you don't want to store the conversation, set `store` to `false`. The response will not include a `response_id`.

```lms_code_snippet
title: Stateless chat
variants:
  curl:
    language: bash
    code: |
      curl http://localhost:1234/api/v1/chat \
        -H "Authorization: Bearer $LM_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "ibm/granite-4-micro",
          "input": "Tell me a joke.",
          "store": false
        }'
```

This is useful for one-off requests where you don't need to maintain context.


Streaming events let you render chat responses incrementally over Server‑Sent Events (SSE). When you call `POST /api/v1/chat` with `stream: true`, the server emits a series of named events that you can consume. These events arrive in order and may include multiple deltas (for reasoning and message content), tool call boundaries and payloads, and any errors encountered. The stream always begins with `chat.start` and concludes with `chat.end`, which contains the aggregated result equivalent to a non‑streaming response.

List of event types that can be sent in an `/api/v1/chat` response stream:
- `chat.start`
- `model_load.start`
- `model_load.progress`
- `model_load.end`
- `prompt_processing.start`
- `prompt_processing.progress`
- `prompt_processing.end`
- `reasoning.start`
- `reasoning.delta`
- `reasoning.end`
- `tool_call.start`
- `tool_call.arguments`
- `tool_call.success`
- `tool_call.failure`
- `message.start`
- `message.delta`
- `message.end`
- `error`
- `chat.end`

Events will be streamed out in the following raw format:
```bash
event: <event type>
data: <JSON event data>
```

### `chat.start`
````lms_hstack
An event that is emitted at the start of a chat response stream.
```lms_params
- name: model_instance_id
  type: string
  description: Unique identifier for the loaded model instance that will generate the response.
- name: type
  type: '"chat.start"'
  description: The type of the event. Always `chat.start`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "chat.start",
        "model_instance_id": "openai/gpt-oss-20b"
      }
```
````

### `model_load.start`
````lms_hstack
Signals the start of a model being loaded to fulfill the chat request. Will not be emitted if the requested model is already loaded.
```lms_params
- name: model_instance_id
  type: string
  description: Unique identifier for the model instance being loaded.
- name: type
  type: '"model_load.start"'
  description: The type of the event. Always `model_load.start`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "model_load.start",
        "model_instance_id": "openai/gpt-oss-20b"
      }
```
````

### `model_load.progress`
````lms_hstack
Progress of the model load.
```lms_params
- name: model_instance_id
  type: string
  description: Unique identifier for the model instance being loaded.
- name: progress
  type: number
  description: Progress of the model load as a float between `0` and `1`.
- name: type
  type: '"model_load.progress"'
  description: The type of the event. Always `model_load.progress`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "model_load.progress",
        "model_instance_id": "openai/gpt-oss-20b",
        "progress": 0.65
      }
```
````

### `model_load.end`
````lms_hstack
Signals a successfully completed model load.
```lms_params
- name: model_instance_id
  type: string
  description: Unique identifier for the model instance that was loaded.
- name: load_time_seconds
  type: number
  description: Time taken to load the model in seconds.
- name: type
  type: '"model_load.end"'
  description: The type of the event. Always `model_load.end`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "model_load.end",
        "model_instance_id": "openai/gpt-oss-20b",
        "load_time_seconds": 12.34
      }
```
````

### `prompt_processing.start`
````lms_hstack
Signals the start of the model processing a prompt.
```lms_params
- name: type
  type: '"prompt_processing.start"'
  description: The type of the event. Always `prompt_processing.start`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "prompt_processing.start"
      }
```
````

### `prompt_processing.progress`
````lms_hstack
Progress of the model processing a prompt.
```lms_params
- name: progress
  type: number
  description: Progress of the prompt processing as a float between `0` and `1`.
- name: type
  type: '"prompt_processing.progress"'
  description: The type of the event. Always `prompt_processing.progress`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "prompt_processing.progress",
        "progress": 0.5
      }
```
````

### `prompt_processing.end`
````lms_hstack
Signals the end of the model processing a prompt.
```lms_params
- name: type
  type: '"prompt_processing.end"'
  description: The type of the event. Always `prompt_processing.end`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "prompt_processing.end"
      }
```
````

### `reasoning.start`
````lms_hstack
Signals the model is starting to stream reasoning content.
```lms_params
- name: type
  type: '"reasoning.start"'
  description: The type of the event. Always `reasoning.start`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "reasoning.start"
      }
```
````

### `reasoning.delta`
````lms_hstack
A chunk of reasoning content. Multiple deltas may arrive.
```lms_params
- name: content
  type: string
  description: Reasoning text fragment.
- name: type
  type: '"reasoning.delta"'
  description: The type of the event. Always `reasoning.delta`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "reasoning.delta",
        "content": "Need to"
      }
```
````

### `reasoning.end`
````lms_hstack
Signals the end of the reasoning stream.
```lms_params
- name: type
  type: '"reasoning.end"'
  description: The type of the event. Always `reasoning.end`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "reasoning.end"
      }
```
````

### `tool_call.start`
````lms_hstack
Emitted when the model starts a tool call.
```lms_params
- name: tool
  type: string
  description: Name of the tool being called.
- name: provider_info
  type: object
  description: Information about the tool provider. Discriminated union upon possible provider types.
  children:
    - name: Plugin provider info
      type: object
      description: Present when the tool is provided by a plugin.
      children:
        - name: type
          type: '"plugin"'
          description: Provider type.
        - name: plugin_id
          type: string
          description: Identifier of the plugin.
    - name: Ephemeral MCP provider info
      type: object
      description: Present when the tool is provided by a ephemeral MCP server.
      children:
        - name: type
          type: '"ephemeral_mcp"'
          description: Provider type.
        - name: server_label
          type: string
          description: Label of the MCP server.
- name: type
  type: '"tool_call.start"'
  description: The type of the event. Always `tool_call.start`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "tool_call.start",
        "tool": "model_search",
        "provider_info": {
          "type": "ephemeral_mcp",
          "server_label": "huggingface"
        }
      }
```
````

### `tool_call.arguments`
````lms_hstack
Arguments streamed for the current tool call.
```lms_params
- name: tool
  type: string
  description: Name of the tool being called.
- name: arguments
  type: object
  description: Arguments passed to the tool. Can have any keys/values depending on the tool definition.
- name: provider_info
  type: object
  description: Information about the tool provider. Discriminated union upon possible provider types.
  children:
    - name: Plugin provider info
      type: object
      description: Present when the tool is provided by a plugin.
      children:
        - name: type
          type: '"plugin"'
          description: Provider type.
        - name: plugin_id
          type: string
          description: Identifier of the plugin.
    - name: Ephemeral MCP provider info
      type: object
      description: Present when the tool is provided by a ephemeral MCP server.
      children:
        - name: type
          type: '"ephemeral_mcp"'
          description: Provider type.
        - name: server_label
          type: string
          description: Label of the MCP server.
- name: type
  type: '"tool_call.arguments"'
  description: The type of the event. Always `tool_call.arguments`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "tool_call.arguments",
        "tool": "model_search",
        "arguments": {
          "sort": "trendingScore",
          "limit": 1
        },
        "provider_info": {
          "type": "ephemeral_mcp",
          "server_label": "huggingface"
        }
      }
```
````

### `tool_call.success`
````lms_hstack
Result of the tool call, along with the arguments used.
```lms_params
- name: tool
  type: string
  description: Name of the tool that was called.
- name: arguments
  type: object
  description: Arguments that were passed to the tool.
- name: output
  type: string
  description: Raw tool output string.
- name: provider_info
  type: object
  description: Information about the tool provider. Discriminated union upon possible provider types.
  children:
    - name: Plugin provider info
      type: object
      description: Present when the tool is provided by a plugin.
      children:
        - name: type
          type: '"plugin"'
          description: Provider type.
        - name: plugin_id
          type: string
          description: Identifier of the plugin.
    - name: Ephemeral MCP provider info
      type: object
      description: Present when the tool is provided by a ephemeral MCP server.
      children:
        - name: type
          type: '"ephemeral_mcp"'
          description: Provider type.
        - name: server_label
          type: string
          description: Label of the MCP server.
- name: type
  type: '"tool_call.success"'
  description: The type of the event. Always `tool_call.success`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "tool_call.success",
        "tool": "model_search",
        "arguments": {
          "sort": "trendingScore",
          "limit": 1
        },
        "output": "[{\"type\":\"text\",\"text\":\"Showing first 1 models...\"}]",
        "provider_info": {
          "type": "ephemeral_mcp",
          "server_label": "huggingface"
        }
      }
```
````


### `tool_call.failure`
````lms_hstack
Indicates that the tool call failed.
```lms_params
- name: reason
  type: string
  description: Reason for the tool call failure.
- name: metadata
  type: object
  description: Metadata about the invalid tool call.
  children:
    - name: type
      type: '"invalid_name" | "invalid_arguments"'
      description: Type of error that occurred.
    - name: tool_name
      type: string
      description: Name of the tool that was attempted to be called.
    - name: arguments
      type: object
      optional: true
      description: Arguments that were passed to the tool (only present for `invalid_arguments` errors).
    - name: provider_info
      type: object
      optional: true
      description: Information about the tool provider (only present for `invalid_arguments` errors).
      children:
        - name: type
          type: '"plugin" | "ephemeral_mcp"'
          description: Provider type.
        - name: plugin_id
          type: string
          optional: true
          description: Identifier of the plugin (when `type` is `"plugin"`).
        - name: server_label
          type: string
          optional: true
          description: Label of the MCP server (when `type` is `"ephemeral_mcp"`).
- name: type
  type: '"tool_call.failure"'
  description: The type of the event. Always `tool_call.failure`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "tool_call.failure",
        "reason": "Cannot find tool with name open_browser.",
        "metadata": {
          "type": "invalid_name",
          "tool_name": "open_browser"
        }
      }
```
````

### `message.start`
````lms_hstack
Signals the model is about to stream a message.
```lms_params
- name: type
  type: '"message.start"'
  description: The type of the event. Always `message.start`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "message.start"
      }
```
````

### `message.delta`
````lms_hstack
A chunk of message content. Multiple deltas may arrive.
```lms_params
- name: content
  type: string
  description: Message text fragment.
- name: type
  type: '"message.delta"'
  description: The type of the event. Always `message.delta`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "message.delta",
        "content": "The current"
      }
```
````

### `message.end`
````lms_hstack
Signals the end of the message stream.
```lms_params
- name: type
  type: '"message.end"'
  description: The type of the event. Always `message.end`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "message.end"
      }
```
````

### `error`
````lms_hstack
An error occurred during streaming. The final payload will still be sent in `chat.end` with whatever was generated.
```lms_params
- name: error
  type: object
  description: Error information.
  children:
    - name: type
      type: '"invalid_request" | "unknown" | "mcp_connection_error" | "plugin_connection_error" | "not_implemented" | "model_not_found" | "job_not_found" | "internal_error"'
      description: High-level error type.
    - name: message
      type: string
      description: Human-readable error message.
    - name: code
      type: string
      optional: true
      description: More detailed error code (e.g., validation issue code).
    - name: param
      type: string
      optional: true
      description: Parameter associated with the error, if applicable.
- name: type
  type: '"error"'
  description: The type of the event. Always `error`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "error",
        "error": {
          "type": "invalid_request",
          "message": "\"model\" is required",
          "code": "missing_required_parameter",
          "param": "model"
        }
      }
```
````

### `chat.end`
````lms_hstack
Final event containing the full aggregated response, equivalent to the non-streaming `POST /api/v1/chat` response body.
```lms_params
- name: result
  type: object
  description: Final response with `model_instance_id`, `output`, `stats`, and optional `response_id`. See [non-streaming chat docs](/docs/developer/rest/chat) for more details.
- name: type
  type: '"chat.end"'
  description: The type of the event. Always `chat.end`.
```
:::split:::
```lms_code_snippet
title: Example Event Data
variants:
  json:
    language: json
    code: |
      {
        "type": "chat.end",
        "result": {
          "model_instance_id": "openai/gpt-oss-20b",
          "output": [
            { "type": "reasoning", "content": "Need to call function." },
            {
              "type": "tool_call",
              "tool": "model_search",
              "arguments": { "sort": "trendingScore", "limit": 1 },
              "output": "[{\"type\":\"text\",\"text\":\"Showing first 1 models...\"}]",
              "provider_info": { "type": "ephemeral_mcp", "server_label": "huggingface" }
            },
            { "type": "message", "content": "The current top‑trending model is..." }
          ],
          "stats": {
            "input_tokens": 329,
            "total_output_tokens": 268,
            "reasoning_output_tokens": 5,
            "tokens_per_second": 43.73,
            "time_to_first_token_seconds": 0.781
          },
          "response_id": "resp_02b2017dbc06c12bfc353a2ed6c2b802f8cc682884bb5716"
        }
      }
```
````


````lms_hstack
`POST /api/v1/chat`

**Request body**
```lms_params
- name: model
  type: string
  optional: false
  description: Unique identifier for the model to use.
- name: input
  type: string | array<object>
  optional: false
  description: Message to send to the model.
  children:
    - name: Input text
      unstyledName: true
      type: string
      description: Text content of the message.
    - name: Input object
      unstyledName: true
      type: object
      description: Object representing a message with additional metadata.
      children:
        - name: Text Input
          type: object
          optional: true
          description: Text input to provide user messages
          children:
            - name: type
              type: '"message"'
              optional: false
              description: Type of input item.
            - name: content
              type: string
              description: Text content of the message.
              optional: false
        - name: Image Input
          type: object
          optional: true
          description: Image input to provide user messages
          children:
            - name: type
              type: '"image"'
              optional: false
              description: Type of input item.
            - name: data_url
              type: string
              description: Image data as a base64-encoded data URL.
              optional: false
- name: system_prompt
  type: string
  optional: true
  description: System message that sets model behavior or instructions.
- name: integrations
  type: array<string | object>
  optional: true
  description: List of integrations (plugins, ephemeral MCP servers, etc...) to enable for this request.
  children:
    - name: Plugin id
      unstyledName: true
      type: string
      description: Unique identifier of a plugin to use. Plugins contain `mcp.json` installed MCP servers (id `mcp/<server_label>`). Shorthand for plugin object with no custom configuration.
    - name: Plugin
      unstyledName: true
      type: object
      description: Specification of a plugin to use. Plugins contain `mcp.json` installed MCP servers (id `mcp/<server_label>`).
      children:
        - name: type
          type: '"plugin"'
          optional: false
          description: Type of integration.
        - name: id
          type: string
          optional: false
          description: Unique identifier of the plugin.
        - name: allowed_tools
          type: array<string>
          optional: true
          description: List of tool names the model can call from this plugin. If not provided, all tools from the plugin are allowed.
    - name: Ephemeral MCP server specification
      unstyledName: true
      type: object
      description: Specification of an ephemeral MCP server. Allows defining MCP servers on-the-fly without needing to pre-configure them in your `mcp.json`.
      children:
        - name: type
          type: '"ephemeral_mcp"'
          optional: false
          description: Type of integration.
        - name: server_label
          type: string
          optional: false
          description: Label to identify the MCP server.
        - name: server_url
          type: string
          optional: false
          description: URL of the MCP server.
        - name: allowed_tools
          type: array<string>
          optional: true
          description: List of tool names the model can call from this server. If not provided, all tools from the server are allowed.
        - name: headers
          type: object
          optional: true
          description: Custom HTTP headers to send with requests to the server.
- name: stream
  type: boolean
  optional: true
  description: Whether to stream partial outputs via SSE. Default `false`. See [streaming events](/docs/developer/rest/streaming-events) for more information.
- name: temperature
  type: number
  optional: true
  description: Randomness in token selection. 0 is deterministic, higher values increase creativity [0,1].
- name: top_p
  type: number
  optional: true
  description: Minimum cumulative probability for the possible next tokens [0,1].
- name: top_k
  type: integer
  optional: true
  description: Limits next token selection to top-k most probable tokens.
- name: min_p
  type: number
  optional: true
  description: Minimum base probability for a token to be selected for output [0,1].
- name: repeat_penalty
  type: number
  optional: true
  description: Penalty for repeating token sequences. 1 is no penalty, higher values discourage repetition.
- name: max_output_tokens
  type: integer
  optional: true
  description: Maximum number of tokens to generate.
- name: reasoning
  type: '"off" | "low" | "medium" | "high" | "on"'
  optional: true
  description: Reasoning setting. Will error if the model being used does not support the reasoning setting using. Defaults to the automatically chosen setting for the model.
- name: context_length
  type: integer
  optional: true
  description: Number of tokens to consider as context. Higher values recommended for MCP usage.
- name: store
  type: boolean
  optional: true
  description: Whether to store the chat. If set, response will return a `"response_id"` field. Default `true`.
- name: previous_response_id
  type: string
  optional: true
  description: Identifier of existing response to append to. Must start with `"resp_"`.
```
:::split:::
```lms_code_snippet
variants:
  Request with MCP:
    language: bash
    code: |
      curl http://localhost:1234/api/v1/chat \
        -H "Authorization: Bearer $LM_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "ibm/granite-4-micro",
          "input": "Tell me the top trending model on hugging face and navigate to https://lmstudio.ai",
          "integrations": [
            {
              "type": "ephemeral_mcp",
              "server_label": "huggingface",
              "server_url": "https://huggingface.co/mcp",
              "allowed_tools": [
                "model_search"
              ]
            },
            {
              "type": "plugin",
              "id": "mcp/playwright",
              "allowed_tools": [
                "browser_navigate"
              ]
            }
          ],
          "context_length": 8000,
          "temperature": 0
        }'
  Request with Images:
    language: bash
    code: |
      # Image is a small red square encoded as a base64 data URL
      curl http://localhost:1234/api/v1/chat \
        -H "Authorization: Bearer $LM_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{
          "model": "qwen/qwen3-vl-4b",
          "input": [
            {
              "type": "text",
              "content": "Describe this image in two sentences"
            },
            {
              "type": "image",
              "data_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mP8z8BQz0AEYBxVSF+FABJADveWkH6oAAAAAElFTkSuQmCC"
            }
          ],
          "context_length": 2048,
          "temperature": 0
        }'
```
````

---

````lms_hstack
**Response fields**
```lms_params
- name: model_instance_id
  type: string
  description: Unique identifier for the loaded model instance that generated the response.
- name: output
  type: array<object>
  description: Array of output items generated. Each item can be one of three types.
  children:
    - name: Message
      unstyledName: true
      type: object
      description: A text message from the model.
      children:
        - name: type
          type: '"message"'
          description: Type of output item.
        - name: content
          type: string
          description: Text content of the message.
    - name: Tool call
      unstyledName: true
      type: object
      description: A tool call made by the model.
      children:
        - name: type
          type: '"tool_call"'
          description: Type of output item.
        - name: tool
          type: string
          description: Name of the tool called.
        - name: arguments
          type: object
          description: Arguments passed to the tool. Can have any keys/values depending on the tool definition.
        - name: output
          type: string
          description: Result returned from the tool.
        - name: provider_info
          type: object
          description: Information about the tool provider.
          children:
            - name: type
              type: '"plugin" | "ephemeral_mcp"'
              description: Provider type.
            - name: plugin_id
              type: string
              optional: true
              description: Identifier of the plugin (when `type` is `"plugin"`).
            - name: server_label
              type: string
              optional: true
              description: Label of the MCP server (when `type` is `"ephemeral_mcp"`).
    - name: Reasoning
      unstyledName: true
      type: object
      description: Reasoning content from the model.
      children:
        - name: type
          type: '"reasoning"'
          description: Type of output item.
        - name: content
          type: string
          description: Text content of the reasoning.
    - name: Invalid tool call
      unstyledName: true
      type: object
      description: An invalid tool call made by the model - due to invalid tool name or tool arguments.
      children:
        - name: type
          type: '"invalid_tool_call"'
          description: Type of output item.
        - name: reason
          type: string
          description: Reason why the tool call was invalid.
        - name: metadata
          type: object
          description: Metadata about the invalid tool call.
          children:
            - name: type
              type: '"invalid_name" | "invalid_arguments"'
              description: Type of error that occurred.
            - name: tool_name
              type: string
              description: Name of the tool that was attempted to be called.
            - name: arguments
              type: object
              optional: true
              description: Arguments that were passed to the tool (only present for `invalid_arguments` errors).
            - name: provider_info
              type: object
              optional: true
              description: Information about the tool provider (only present for `invalid_arguments` errors).
              children:
                - name: type
                  type: '"plugin" | "ephemeral_mcp"'
                  description: Provider type.
                - name: plugin_id
                  type: string
                  optional: true
                  description: Identifier of the plugin (when `type` is `"plugin"`).
                - name: server_label
                  type: string
                  optional: true
                  description: Label of the MCP server (when `type` is `"ephemeral_mcp"`).
- name: stats
  type: object
  description: Token usage and performance metrics.
  children:
    - name: input_tokens
      type: number
      description: Number of input tokens. Includes formatting, tool definitions, and prior messages in the chat.
    - name: total_output_tokens
      type: number
      description: Total number of output tokens generated.
    - name: reasoning_output_tokens
      type: number
      description: Number of tokens used for reasoning.
    - name: tokens_per_second
      type: number
      description: Generation speed in tokens per second.
    - name: time_to_first_token_seconds
      type: number
      description: Time in seconds to generate the first token.
    - name: model_load_time_seconds
      type: number
      optional: true
      description: Time taken to load the model for this request in seconds. Present only if the model was not already loaded.
- name: response_id
  type: string
  optional: true
  description: Identifier of the response for subsequent requests. Starts with `"resp_"`. Present when `store` is `true`.
```
:::split:::
```lms_code_snippet
variants:
  Request with MCP:
    language: json
    code: |
      {
        "model_instance_id": "ibm/granite-4-micro",
        "output": [
          {
            "type": "tool_call",
            "tool": "model_search",
            "arguments": {
              "sort": "trendingScore",
              "query": "",
              "limit": 1
            },
            "output": "...",
            "provider_info": {
              "server_label": "huggingface",
              "type": "ephemeral_mcp"
            }
          },
          {
            "type": "message",
            "content": "..."
          },
          {
            "type": "tool_call",
            "tool": "browser_navigate",
            "arguments": {
              "url": "https://lmstudio.ai"
            },
            "output": "...",
            "provider_info": {
              "plugin_id": "mcp/playwright",
              "type": "plugin"
            }
          },
          {
            "type": "message",
            "content": "**Top Trending Model on Hugging Face** ... Below is a quick snapshot of what’s on the landing page ... more details on the model or LM Studio itself!"
          }
        ],
        "stats": {
          "input_tokens": 646,
          "total_output_tokens": 586,
          "reasoning_output_tokens": 0,
          "tokens_per_second": 29.753900615398926,
          "time_to_first_token_seconds": 1.088,
          "model_load_time_seconds": 2.656
        },
        "response_id": "resp_4ef013eba0def1ed23f19dde72b67974c579113f544086de"
      }
  Request with Images:
    language: json
    code: |
      {
        "model_instance_id": "qwen/qwen3-vl-4b",
        "output": [
          {
            "type": "message",
            "content": "This image is a solid, vibrant red square that fills the entire frame, with no discernible texture, pattern, or other elements. It presents a minimalist, uniform visual field of pure red, evoking a sense of boldness or urgency."
          }
        ],
        "stats": {
          "input_tokens": 17,
          "total_output_tokens": 50,
          "reasoning_output_tokens": 0,
          "tokens_per_second": 51.03762685242662,
          "time_to_first_token_seconds": 0.814
        },
        "response_id": "resp_0182bd7c479d7451f9a35471f9c26b34de87a7255856b9a4"
      }
```
````


Tool use enables LLMs to request calls to external functions and APIs through the `/v1/chat/completions` and `v1/responses` endpoints ([Learn more](/docs/developer/openai-compat)), via LM Studio's REST API (or via any OpenAI client). This expands their functionality far beyond text output.

<hr>

## Quick Start

### 1. Start LM Studio as a server

To use LM Studio programmatically from your own code, run LM Studio as a local server.

You can turn on the server from the "Developer" tab in LM Studio, or via the `lms` CLI:

```bash
lms server start
```

###### Install `lms` by running `npx lmstudio install-cli`

This will allow you to interact with LM Studio via the REST API. For an intro to LM Studio's REST API, see [REST API Overview](/docs/developer/rest).

### 2. Load a Model

You can load a model from the "Chat" or "Developer" tabs in LM Studio, or via the `lms` CLI:

```bash
lms load
```

### 3. Copy, Paste, and Run an Example!

- `Curl`
  - [Single Turn Tool Call Request](#example-using-curl)
- `Python`
  - [Single Turn Tool Call + Tool Use](#single-turn-example)
  - [Multi-Turn Example](#multi-turn-example)
  - [Advanced Agent Example](#advanced-agent-example)

## Tool Use

### What really is "Tool Use"?

Tool use describes:

- LLMs output text requesting functions to be called (LLMs cannot directly execute code)
- Your code executes those functions
- Your code feeds the results back to the LLM.

### High-level flow

```xml
┌──────────────────────────┐
│ SETUP: LLM + Tool list   │
└──────────┬───────────────┘
           ▼
┌──────────────────────────┐
│    Get user input        │◄────┐
└──────────┬───────────────┘     │
           ▼                     │
┌──────────────────────────┐     │
│ LLM prompted w/messages  │     │
└──────────┬───────────────┘     │
           ▼                     │
     Needs tools?                │
      │         │                │
    Yes         No               │
      │         │                │
      ▼         └────────────┐   │
┌─────────────┐              │   │
│Tool Response│              │   │
└──────┬──────┘              │   │
       ▼                     │   │
┌─────────────┐              │   │
│Execute tools│              │   │
└──────┬──────┘              │   │
       ▼                     ▼   │
┌─────────────┐          ┌───────────┐
│Add results  │          │  Normal   │
│to messages  │          │ response  │
└──────┬──────┘          └─────┬─────┘
       │                       ▲
       └───────────────────────┘
```

### In-depth flow

LM Studio supports tool use through the `/v1/chat/completions` endpoint when given function definitions in the `tools` parameter of the request body. Tools are specified as an array of function definitions that describe their parameters and usage, like:

It follows the same format as OpenAI's [Function Calling](https://platform.openai.com/docs/guides/function-calling) API and is expected to work via the OpenAI client SDKs.

We will use [lmstudio-community/Qwen2.5-7B-Instruct-GGUF](https://model.lmstudio.ai/download/lmstudio-community/Qwen2.5-7B-Instruct-GGUF) as the model in this example flow.

1. You provide a list of tools to an LLM. These are the tools that the model can _request_ calls to.
   For example:

```json
// the list of tools is model-agnostic
[
  {
    "type": "function",
    "function": {
      "name": "get_delivery_date",
      "description": "Get the delivery date for a customer's order",
      "parameters": {
        "type": "object",
        "properties": {
          "order_id": {
            "type": "string"
          }
        },
        "required": ["order_id"]
      }
    }
  }
]
```

This list will be injected into the `system` prompt of the model depending on the model's chat template. For `Qwen2.5-Instruct`, this looks like:

```json
<|im_start|>system
You are Qwen, created by Alibaba Cloud. You are a helpful assistant.

# Tools

You may call one or more functions to assist with the user query.

You are provided with function signatures within <tools></tools> XML tags:
<tools>
{"type": "function", "function": {"name": "get_delivery_date", "description": "Get the delivery date for a customer's order", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}}
</tools>

For each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:
<tool_call>
{"name": <function-name>, "arguments": <args-json-object>}
</tool_call><|im_end|>
```

**Important**: The model can only _request_ calls to these tools because LLMs _cannot_ directly call functions, APIs, or any other tools. They can only output text, which can then be parsed to programmatically call the functions.

2. When prompted, the LLM can then decide to either:

   - (a) Call one or more tools

   ```xml
   User: Get me the delivery date for order 123
   Model: <tool_call>
   {"name": "get_delivery_date", "arguments": {"order_id": "123"}}
   </tool_call>
   ```

   - (b) Respond normally

   ```xml
   User: Hi
   Model: Hello! How can I assist you today?
   ```

3. LM Studio parses the text output from the model into an OpenAI-compliant `chat.completion` response object.

   - If the model was given access to `tools`, LM Studio will attempt to parse the tool calls into the `response.choices[0].message.tool_calls` field of the `chat.completion` response object.
   - If LM Studio cannot parse any **correctly formatted** tool calls, it will simply return the response to the standard `response.choices[0].message.content` field.
   - **Note**: Smaller models and models that were not trained for tool use may output improperly formatted tool calls, resulting in LM Studio being unable to parse them into the `tool_calls` field. This is useful for troubleshooting when you do not receive `tool_calls` as expected. Example of an improperly formatting `Qwen2.5-Instruct` tool call:

   ```xml
   <tool_call>
   ["name": "get_delivery_date", function: "date"]
   </tool_call>
   ```

   > Note that the brackets are incorrect, and the call does not follow the `name, argument` format.

4. Your code parses the `chat.completion` response to check for tool calls from the model, then calls the appropriate tools with the parameters specified by the model. Your code then adds both:

   1. The model's tool call message
   2. The result of the tool call

   To the `messages` array to send back to the model

   ```python
   # pseudocode, see examples for copy-paste snippets
   if response.has_tool_calls:
       for each tool_call:
           # Extract function name & args
           function_to_call = tool_call.name     # e.g. "get_delivery_date"
           args = tool_call.arguments            # e.g. {"order_id": "123"}

           # Execute the function
           result = execute_function(function_to_call, args)

           # Add result to conversation
           add_to_messages([
               ASSISTANT_TOOL_CALL_MESSAGE,      # The request to use the tool
               TOOL_RESULT_MESSAGE               # The tool's response
           ])
   else:
       # Normal response without tools
       add_to_messages(response.content)
   ```

5. The LLM is then prompted again with the updated messages array, but without access to tools. This is because:
   - The LLM already has the tool results in the conversation history
   - We want the LLM to provide a final response to the user, not call more tools
   ```python
   # Example messages
   messages = [
       {"role": "user", "content": "When will order 123 be delivered?"},
       {"role": "assistant", "function_call": {
           "name": "get_delivery_date",
           "arguments": {"order_id": "123"}
       }},
       {"role": "tool", "content": "2024-03-15"},
   ]
   response = client.chat.completions.create(
       model="lmstudio-community/qwen2.5-7b-instruct",
       messages=messages
   )
   ```
   The `response.choices[0].message.content` field after this call may be something like:
   ```xml
   Your order #123 will be delivered on March 15th, 2024
   ```
6. The loop continues back at step 2 of the flow

Note: This is the `pedantic` flow for tool use. However, you can certainly experiment with this flow to best fit your use case.

## Supported Models

Through LM Studio, **all** models support at least some degree of tool use.

However, there are currently two levels of support that may impact the quality of the experience: Native and Default.

Models with Native tool use support will have a hammer badge in the app, and generally perform better in tool use scenarios.

### Native tool use support

"Native" tool use support means that both:

1. The model has a chat template that supports tool use (usually means the model has been trained for tool use)
   - This is what will be used to format the `tools` array into the system prompt and tell them model how to format tool calls
   - Example: [Qwen2.5-Instruct chat template](https://huggingface.co/mlx-community/Qwen2.5-7B-Instruct-4bit/blob/c26a38f6a37d0a51b4e9a1eb3026530fa35d9fed/tokenizer_config.json#L197)
2. LM Studio supports that model's tool use format
   - Required for LM Studio to properly input the chat history into the chat template, and parse the tool calls the model outputs into the `chat.completion` object

Models that currently have native tool use support in LM Studio (subject to change):

- Qwen
  - `GGUF` [lmstudio-community/Qwen2.5-7B-Instruct-GGUF](https://model.lmstudio.ai/download/lmstudio-community/Qwen2.5-7B-Instruct-GGUF) (4.68 GB)
  - `MLX` [mlx-community/Qwen2.5-7B-Instruct-4bit](https://model.lmstudio.ai/download/mlx-community/Qwen2.5-7B-Instruct-4bit) (4.30 GB)
- Llama-3.1, Llama-3.2
  - `GGUF` [lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF](https://model.lmstudio.ai/download/lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF) (4.92 GB)
  - `MLX` [mlx-community/Meta-Llama-3.1-8B-Instruct-8bit](https://model.lmstudio.ai/download/mlx-community/Meta-Llama-3.1-8B-Instruct-8bit) (8.54 GB)
- Mistral
  - `GGUF` [bartowski/Ministral-8B-Instruct-2410-GGUF](https://model.lmstudio.ai/download/bartowski/Ministral-8B-Instruct-2410-GGUF) (4.67 GB)
  - `MLX` [mlx-community/Ministral-8B-Instruct-2410-4bit](https://model.lmstudio.ai/download/mlx-community/Ministral-8B-Instruct-2410-4bit) (4.67 GB GB)

### Default tool use support

"Default" tool use support means that **either**:

1. The model does not have chat template that supports tool use (usually means the model has not been trained for tool use)
2. LM Studio does not currently support that model's tool use format

Under the hood, default tool use works by:

- Giving models a custom system prompt and a default tool call format to use
- Converting `tool` role messages to the `user` role so that chat templates without the `tool` role are compatible
- Converting `assistant` role `tool_calls` into the default tool call format

Results will vary by model.

You can see the default format by running `lms log stream` in your terminal, then sending a chat completion request with `tools` to a model that doesn't have Native tool use support. The default format is subject to change.

<details>
<summary>Expand to see example of default tool use format</summary>

```bash
-> % lms log stream
Streaming logs from LM Studio

timestamp: 11/13/2024, 9:35:15 AM
type: llm.prediction.input
modelIdentifier: gemma-2-2b-it
modelPath: lmstudio-community/gemma-2-2b-it-GGUF/gemma-2-2b-it-Q4_K_M.gguf
input: "<start_of_turn>system
You are a tool-calling AI. You can request calls to available tools with this EXACT format:
[TOOL_REQUEST]{"name": "tool_name", "arguments": {"param1": "value1"}}[END_TOOL_REQUEST]

AVAILABLE TOOLS:
{
  "type": "toolArray",
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_delivery_date",
        "description": "Get the delivery date for a customer's order",
        "parameters": {
          "type": "object",
          "properties": {
            "order_id": {
              "type": "string"
            }
          },
          "required": [
            "order_id"
          ]
        }
      }
    }
  ]
}

RULES:
- Only use tools from AVAILABLE TOOLS
- Include all required arguments
- Use one [TOOL_REQUEST] block per tool
- Never use [TOOL_RESULT]
- If you decide to call one or more tools, there should be no other text in your message

Examples:
"Check Paris weather"
[TOOL_REQUEST]{"name": "get_weather", "arguments": {"location": "Paris"}}[END_TOOL_REQUEST]

"Send email to John about meeting and open browser"
[TOOL_REQUEST]{"name": "send_email", "arguments": {"to": "John", "subject": "meeting"}}[END_TOOL_REQUEST]
[TOOL_REQUEST]{"name": "open_browser", "arguments": {}}[END_TOOL_REQUEST]

Respond conversationally if no matching tools exist.<end_of_turn>
<start_of_turn>user
Get me delivery date for order 123<end_of_turn>
<start_of_turn>model
"
```

If the model follows this format exactly to call tools, i.e:

```
[TOOL_REQUEST]{"name": "get_delivery_date", "arguments": {"order_id": "123"}}[END_TOOL_REQUEST]
```

Then LM Studio will be able to parse those tool calls into the `chat.completions` object, just like for natively supported models.

</details>

All models that don't have native tool use support will have default tool use support.

## Example using `curl`

This example demonstrates a model requesting a tool call using the `curl` utility.

To run this example on Mac or Linux, use any terminal. On Windows, use [Git Bash](https://git-scm.com/download/win).

```bash
curl http://localhost:1234/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "lmstudio-community/qwen2.5-7b-instruct",
    "messages": [{"role": "user", "content": "What dell products do you have under $50 in electronics?"}],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "search_products",
          "description": "Search the product catalog by various criteria. Use this whenever a customer asks about product availability, pricing, or specifications.",
          "parameters": {
            "type": "object",
            "properties": {
              "query": {
                "type": "string",
                "description": "Search terms or product name"
              },
              "category": {
                "type": "string",
                "description": "Product category to filter by",
                "enum": ["electronics", "clothing", "home", "outdoor"]
              },
              "max_price": {
                "type": "number",
                "description": "Maximum price in dollars"
              }
            },
            "required": ["query"],
            "additionalProperties": false
          }
        }
      }
    ]
  }'
```

All parameters recognized by `/v1/chat/completions` will be honored, and the array of available tools should be provided in the `tools` field.

If the model decides that the user message would be best fulfilled with a tool call, an array of tool call request objects will be provided in the response field, `choices[0].message.tool_calls`.

The `finish_reason` field of the top-level response object will also be populated with `"tool_calls"`.

An example response to the above `curl` request will look like:

```bash
{
  "id": "chatcmpl-gb1t1uqzefudice8ntxd9i",
  "object": "chat.completion",
  "created": 1730913210,
  "model": "lmstudio-community/qwen2.5-7b-instruct",
  "choices": [
    {
      "index": 0,
      "logprobs": null,
      "finish_reason": "tool_calls",
      "message": {
        "role": "assistant",
        "tool_calls": [
          {
            "id": "365174485",
            "type": "function",
            "function": {
              "name": "search_products",
              "arguments": "{\"query\":\"dell\",\"category\":\"electronics\",\"max_price\":50}"
            }
          }
        ]
      }
    }
  ],
  "usage": {
    "prompt_tokens": 263,
    "completion_tokens": 34,
    "total_tokens": 297
  },
  "system_fingerprint": "lmstudio-community/qwen2.5-7b-instruct"
}
```

In plain english, the above response can be thought of as the model saying:

> "Please call the `search_products` function, with arguments:
>
> - 'dell' for the `query` parameter,
> - 'electronics' for the `category` parameter
> - '50' for the `max_price` parameter
>
> and give me back the results"

The `tool_calls` field will need to be parsed to call actual functions/APIs. The below examples demonstrate how.

## Examples using `python`

Tool use shines when paired with program languages like python, where you can implement the functions specified in the `tools` field to programmatically call them when the model requests.

### Single-turn example

Below is a simple single-turn (model is only called once) example of enabling a model to call a function called `say_hello` that prints a hello greeting to the console:

`single-turn-example.py`

```python
from openai import OpenAI

# Connect to LM Studio
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

# Define a simple function
def say_hello(name: str) -> str:
    print(f"Hello, {name}!")

# Tell the AI about our function
tools = [
    {
        "type": "function",
        "function": {
            "name": "say_hello",
            "description": "Says hello to someone",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The person's name"
                    }
                },
                "required": ["name"]
            }
        }
    }
]

# Ask the AI to use our function
response = client.chat.completions.create(
    model="lmstudio-community/qwen2.5-7b-instruct",
    messages=[{"role": "user", "content": "Can you say hello to Bob the Builder?"}],
    tools=tools
)

# Get the name the AI wants to use a tool to say hello to
# (Assumes the AI has requested a tool call and that tool call is say_hello)
tool_call = response.choices[0].message.tool_calls[0]
name = eval(tool_call.function.arguments)["name"]

# Actually call the say_hello function
say_hello(name) # Prints: Hello, Bob the Builder!

```

Running this script from the console should yield results like:

```xml
-> % python single-turn-example.py
Hello, Bob the Builder!
```

Play around with the name in

```python
messages=[{"role": "user", "content": "Can you say hello to Bob the Builder?"}]
```

to see the model call the `say_hello` function with different names.

### Multi-turn example

Now for a slightly more complex example.

In this example, we'll:

1. Enable the model to call a `get_delivery_date` function
2. Hand the result of calling that function back to the model, so that it can fulfill the user's request in plain text

<details>
<summary><code>multi-turn-example.py</code> (click to expand) </summary>

```python
from datetime import datetime, timedelta
import json
import random
from openai import OpenAI

# Point to the local server
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
model = "lmstudio-community/qwen2.5-7b-instruct"


def get_delivery_date(order_id: str) -> datetime:
    # Generate a random delivery date between today and 14 days from now
    # in a real-world scenario, this function would query a database or API
    today = datetime.now()
    random_days = random.randint(1, 14)
    delivery_date = today + timedelta(days=random_days)
    print(
        f"\nget_delivery_date function returns delivery date:\n\n{delivery_date}",
        flush=True,
    )
    return delivery_date


tools = [
    {
        "type": "function",
        "function": {
            "name": "get_delivery_date",
            "description": "Get the delivery date for a customer's order. Call this whenever you need to know the delivery date, for example when a customer asks 'Where is my package'",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "The customer's order ID.",
                    },
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    }
]

messages = [
    {
        "role": "system",
        "content": "You are a helpful customer support assistant. Use the supplied tools to assist the user.",
    },
    {
        "role": "user",
        "content": "Give me the delivery date and time for order number 1017",
    },
]

# LM Studio
response = client.chat.completions.create(
    model=model,
    messages=messages,
    tools=tools,
)

print("\nModel response requesting tool call:\n", flush=True)
print(response, flush=True)

# Extract the arguments for get_delivery_date
# Note this code assumes we have already determined that the model generated a function call.
tool_call = response.choices[0].message.tool_calls[0]
arguments = json.loads(tool_call.function.arguments)

order_id = arguments.get("order_id")

# Call the get_delivery_date function with the extracted order_id
delivery_date = get_delivery_date(order_id)

assistant_tool_call_request_message = {
    "role": "assistant",
    "tool_calls": [
        {
            "id": response.choices[0].message.tool_calls[0].id,
            "type": response.choices[0].message.tool_calls[0].type,
            "function": response.choices[0].message.tool_calls[0].function,
        }
    ],
}

# Create a message containing the result of the function call
function_call_result_message = {
    "role": "tool",
    "content": json.dumps(
        {
            "order_id": order_id,
            "delivery_date": delivery_date.strftime("%Y-%m-%d %H:%M:%S"),
        }
    ),
    "tool_call_id": response.choices[0].message.tool_calls[0].id,
}

# Prepare the chat completion call payload
completion_messages_payload = [
    messages[0],
    messages[1],
    assistant_tool_call_request_message,
    function_call_result_message,
]

# Call the OpenAI API's chat completions endpoint to send the tool call result back to the model
# LM Studio
response = client.chat.completions.create(
    model=model,
    messages=completion_messages_payload,
)

print("\nFinal model response with knowledge of the tool call result:\n", flush=True)
print(response.choices[0].message.content, flush=True)

```

</details>

Running this script from the console should yield results like:

```xml
-> % python multi-turn-example.py

Model response requesting tool call:

ChatCompletion(id='chatcmpl-wwpstqqu94go4hvclqnpwn', choices=[Choice(finish_reason='tool_calls', index=0, logprobs=None, message=ChatCompletionMessage(content=None, refusal=None, role='assistant', function_call=None, tool_calls=[ChatCompletionMessageToolCall(id='377278620', function=Function(arguments='{"order_id":"1017"}', name='get_delivery_date'), type='function')]))], created=1730916196, model='lmstudio-community/qwen2.5-7b-instruct', object='chat.completion', service_tier=None, system_fingerprint='lmstudio-community/qwen2.5-7b-instruct', usage=CompletionUsage(completion_tokens=24, prompt_tokens=223, total_tokens=247, completion_tokens_details=None, prompt_tokens_details=None))

get_delivery_date function returns delivery date:

2024-11-19 13:03:17.773298

Final model response with knowledge of the tool call result:

Your order number 1017 is scheduled for delivery on November 19, 2024, at 13:03 PM.
```

### Advanced agent example

Building upon the principles above, we can combine LM Studio models with locally defined functions to create an "agent" - a system that pairs a language model with custom functions to understand requests and perform actions beyond basic text generation.

The agent in the below example can:

1. Open safe urls in your default browser
2. Check the current time
3. Analyze directories in your file system

<details>
<summary><code>agent-chat-example.py</code> (click to expand) </summary>

```python
import json
from urllib.parse import urlparse
import webbrowser
from datetime import datetime
import os
from openai import OpenAI

# Point to the local server
client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
model = "lmstudio-community/qwen2.5-7b-instruct"


def is_valid_url(url: str) -> bool:

    try:
        result = urlparse(url)
        return bool(result.netloc)  # Returns True if there's a valid network location
    except Exception:
        return False


def open_safe_url(url: str) -> dict:
    # List of allowed domains (expand as needed)
    SAFE_DOMAINS = {
        "lmstudio.ai",
        "github.com",
        "google.com",
        "wikipedia.org",
        "weather.com",
        "stackoverflow.com",
        "python.org",
        "docs.python.org",
    }

    try:
        # Add http:// if no scheme is present
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url

        # Validate URL format
        if not is_valid_url(url):
            return {"status": "error", "message": f"Invalid URL format: {url}"}

        # Parse the URL and check domain
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        base_domain = ".".join(domain.split(".")[-2:])

        if base_domain in SAFE_DOMAINS:
            webbrowser.open(url)
            return {"status": "success", "message": f"Opened {url} in browser"}
        else:
            return {
                "status": "error",
                "message": f"Domain {domain} not in allowed list",
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_current_time() -> dict:
    """Get the current system time with timezone information"""
    try:
        current_time = datetime.now()
        timezone = datetime.now().astimezone().tzinfo
        formatted_time = current_time.strftime("%Y-%m-%d %H:%M:%S %Z")
        return {
            "status": "success",
            "time": formatted_time,
            "timezone": str(timezone),
            "timestamp": current_time.timestamp(),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def analyze_directory(path: str = ".") -> dict:
    """Count and categorize files in a directory"""
    try:
        stats = {
            "total_files": 0,
            "total_dirs": 0,
            "file_types": {},
            "total_size_bytes": 0,
        }

        for entry in os.scandir(path):
            if entry.is_file():
                stats["total_files"] += 1
                ext = os.path.splitext(entry.name)[1].lower() or "no_extension"
                stats["file_types"][ext] = stats["file_types"].get(ext, 0) + 1
                stats["total_size_bytes"] += entry.stat().st_size
            elif entry.is_dir():
                stats["total_dirs"] += 1
                # Add size of directory contents
                for root, _, files in os.walk(entry.path):
                    for file in files:
                        try:
                            stats["total_size_bytes"] += os.path.getsize(os.path.join(root, file))
                        except (OSError, FileNotFoundError):
                            continue

        return {"status": "success", "stats": stats, "path": os.path.abspath(path)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


tools = [
    {
        "type": "function",
        "function": {
            "name": "open_safe_url",
            "description": "Open a URL in the browser if it's deemed safe",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to open",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current system time with timezone information",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_directory",
            "description": "Analyze the contents of a directory, counting files and folders",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The directory path to analyze. Defaults to current directory if not specified.",
                    },
                },
                "required": [],
            },
        },
    },
]


def process_tool_calls(response, messages):
    """Process multiple tool calls and return the final response and updated messages"""
    # Get all tool calls from the response
    tool_calls = response.choices[0].message.tool_calls

    # Create the assistant message with tool calls
    assistant_tool_call_message = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": tool_call.id,
                "type": tool_call.type,
                "function": tool_call.function,
            }
            for tool_call in tool_calls
        ],
    }

    # Add the assistant's tool call message to the history
    messages.append(assistant_tool_call_message)

    # Process each tool call and collect results
    tool_results = []
    for tool_call in tool_calls:
        # For functions with no arguments, use empty dict
        arguments = (
            json.loads(tool_call.function.arguments)
            if tool_call.function.arguments.strip()
            else {}
        )

        # Determine which function to call based on the tool call name
        if tool_call.function.name == "open_safe_url":
            result = open_safe_url(arguments["url"])
        elif tool_call.function.name == "get_current_time":
            result = get_current_time()
        elif tool_call.function.name == "analyze_directory":
            path = arguments.get("path", ".")
            result = analyze_directory(path)
        else:
            # llm tried to call a function that doesn't exist, skip
            continue

        # Add the result message
        tool_result_message = {
            "role": "tool",
            "content": json.dumps(result),
            "tool_call_id": tool_call.id,
        }
        tool_results.append(tool_result_message)
        messages.append(tool_result_message)

    # Get the final response
    final_response = client.chat.completions.create(
        model=model,
        messages=messages,
    )

    return final_response


def chat():
    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant that can open safe web links, tell the current time, and analyze directory contents. Use these capabilities whenever they might be helpful.",
        }
    ]

    print(
        "Assistant: Hello! I can help you open safe web links, tell you the current time, and analyze directory contents. What would you like me to do?"
    )
    print("(Type 'quit' to exit)")

    while True:
        # Get user input
        user_input = input("\nYou: ").strip()

        # Check for quit command
        if user_input.lower() == "quit":
            print("Assistant: Goodbye!")
            break

        # Add user message to conversation
        messages.append({"role": "user", "content": user_input})

        try:
            # Get initial response
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
            )

            # Check if the response includes tool calls
            if response.choices[0].message.tool_calls:
                # Process all tool calls and get final response
                final_response = process_tool_calls(response, messages)
                print("\nAssistant:", final_response.choices[0].message.content)

                # Add assistant's final response to messages
                messages.append(
                    {
                        "role": "assistant",
                        "content": final_response.choices[0].message.content,
                    }
                )
            else:
                # If no tool call, just print the response
                print("\nAssistant:", response.choices[0].message.content)

                # Add assistant's response to messages
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.choices[0].message.content,
                    }
                )

        except Exception as e:
            print(f"\nAn error occurred: {str(e)}")
            exit(1)


if __name__ == "__main__":
    chat()

```

</details>

Running this script from the console will allow you to chat with the agent:

```xml
-> % python agent-example.py
Assistant: Hello! I can help you open safe web links, tell you the current time, and analyze directory contents. What would you like me to do?
(Type 'quit' to exit)

You: What time is it?

Assistant: The current time is 14:11:40 (EST) as of November 6, 2024.

You: What time is it now?

Assistant: The current time is 14:13:59 (EST) as of November 6, 2024.

You: Open lmstudio.ai

Assistant: The link to lmstudio.ai has been opened in your default web browser.

You: What's in my current directory?

Assistant: Your current directory at `/Users/matt/project` contains a total of 14 files and 8 directories. Here's the breakdown:

- Files without an extension: 3
- `.mjs` files: 2
- `.ts` (TypeScript) files: 3
- Markdown (`md`) file: 1
- JSON files: 4
- TOML file: 1

The total size of these items is 1,566,990,604 bytes.

You: Thank you!

Assistant: You're welcome! If you have any other questions or need further assistance, feel free to ask.

You:
```

### Streaming

When streaming through `/v1/chat/completions` (`stream=true`), tool calls are sent in chunks. Function names and arguments are sent in pieces via `chunk.choices[0].delta.tool_calls.function.name` and `chunk.choices[0].delta.tool_calls.function.arguments`.

For example, to call `get_current_weather(location="San Francisco")`, the streamed `ChoiceDeltaToolCall` in each `chunk.choices[0].delta.tool_calls[0]` object will look like:

```py
ChoiceDeltaToolCall(index=0, id='814890118', function=ChoiceDeltaToolCallFunction(arguments='', name='get_current_weather'), type='function')
ChoiceDeltaToolCall(index=0, id=None, function=ChoiceDeltaToolCallFunction(arguments='{"', name=None), type=None)
ChoiceDeltaToolCall(index=0, id=None, function=ChoiceDeltaToolCallFunction(arguments='location', name=None), type=None)
ChoiceDeltaToolCall(index=0, id=None, function=ChoiceDeltaToolCallFunction(arguments='":"', name=None), type=None)
ChoiceDeltaToolCall(index=0, id=None, function=ChoiceDeltaToolCallFunction(arguments='San Francisco', name=None), type=None)
ChoiceDeltaToolCall(index=0, id=None, function=ChoiceDeltaToolCallFunction(arguments='"}', name=None), type=None)
```

These chunks must be accumulated throughout the stream to form the complete function signature for execution.

The below example shows how to create a simple tool-enhanced chatbot through the `/v1/chat/completions` streaming endpoint (`stream=true`).

<details>
<summary><code>tool-streaming-chatbot.py</code> (click to expand) </summary>

```python
from openai import OpenAI
import time

client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
MODEL = "lmstudio-community/qwen2.5-7b-instruct"

TIME_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "Get the current time, only if asked",
        "parameters": {"type": "object", "properties": {}},
    },
}

def get_current_time():
    return {"time": time.strftime("%H:%M:%S")}

def process_stream(stream, add_assistant_label=True):
    """Handle streaming responses from the API"""
    collected_text = ""
    tool_calls = []
    first_chunk = True

    for chunk in stream:
        delta = chunk.choices[0].delta

        # Handle regular text output
        if delta.content:
            if first_chunk:
                print()
                if add_assistant_label:
                    print("Assistant:", end=" ", flush=True)
                first_chunk = False
            print(delta.content, end="", flush=True)
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

def chat_loop():
    messages = []
    print("Assistant: Hi! I am an AI agent empowered with the ability to tell the current time (Type 'quit' to exit)")

    while True:
        user_input = input("\nYou: ").strip()
        if user_input.lower() == "quit":
            break

        messages.append({"role": "user", "content": user_input})

        # Get initial response
        response_text, tool_calls = process_stream(
            client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=[TIME_TOOL],
                stream=True,
                temperature=0.2
            )
        )

        if not tool_calls:
            print()

        text_in_first_response = len(response_text) > 0
        if text_in_first_response:
            messages.append({"role": "assistant", "content": response_text})

        # Handle tool calls if any
        if tool_calls:
            tool_name = tool_calls[0]["function"]["name"]
            print()
            if not text_in_first_response:
                print("Assistant:", end=" ", flush=True)
            print(f"**Calling Tool: {tool_name}**")
            messages.append({"role": "assistant", "tool_calls": tool_calls})

            # Execute tool calls
            for tool_call in tool_calls:
                if tool_call["function"]["name"] == "get_current_time":
                    result = get_current_time()
                    messages.append({
                        "role": "tool",
                        "content": str(result),
                        "tool_call_id": tool_call["id"]
                    })

            # Get final response after tool execution
            final_response, _ = process_stream(
                client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    stream=True
                ),
                add_assistant_label=False
            )

            if final_response:
                print()
                messages.append({"role": "assistant", "content": final_response})

if __name__ == "__main__":
    chat_loop()
```

</details>

You can chat with the bot by running this script from the console:

```xml
-> % python tool-streaming-chatbot.py
Assistant: Hi! I am an AI agent empowered with the ability to tell the current time (Type 'quit' to exit)

You: Tell me a joke, then tell me the current time

Assistant: Sure! Here's a light joke for you: Why don't scientists trust atoms? Because they make up everything.

Now, let me get the current time for you.

**Calling Tool: get_current_time**

The current time is 18:49:31. Enjoy your day!

You:
```

## Community

Chat with other LM Studio users, discuss LLMs, hardware, and more on the [LM Studio Discord server](https://discord.gg/aPQfnNkxGC).


