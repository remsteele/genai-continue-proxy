import json
import os
import re
from typing import Any
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

app = FastAPI()

def required_env(name: str) -> str:
    """Return a non-empty environment variable with a clear startup error."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value

UPSTREAM_BASE_URL = required_env("UPSTREAM_BASE_URL").rstrip("/")
UPSTREAM_API_KEY = required_env("UPSTREAM_API_KEY")
OUTBOUND_PROXY_URL = os.environ.get("OUTBOUND_PROXY_URL") or None
REASONING_EFFORT = required_env("REASONING_EFFORT")
TOOLS_NOTICE = required_env("TOOLS_NOTICE")

API_KEY_REGEX = re.compile(r'(apiKey:\s*["\'])([^"\']{10,})(["\'])')

def sanitize_content(text: str) -> str:
    """Masks secrets that trigger upstream DLP / content filters."""
    return API_KEY_REGEX.sub(r'\1[REDACTED_API_KEY]\3', text)

def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text", ""))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content) if content is not None else ""

def update_body(body: dict) -> dict:
    messages = body.get("messages", [])
    if not messages:
        return body

    # 1. Apply the deployment's configured reasoning level.
    body["reasoning_effort"] = REASONING_EFFORT

    # 2. Extract and remove the system message (upstream does not accept role: 'system')
    system_text = ""
    if messages[0].get("role") == "system":
        system_msg = messages.pop(0)
        system_text = content_to_text(system_msg.get("content", ""))

    for msg in messages:
        text = content_to_text(msg.get("content", ""))
        msg["content"] = sanitize_content(text)

    # 3. Inject tool specifications into the root user turn if not already present
    first_user_idx = next((i for i, m in enumerate(messages) if m.get("role") == "user"), None)
    if first_user_idx is not None and system_text:
        original = messages[first_user_idx]["content"]
        if "[Client Protocol: Continue Workspace Extension]" not in original:
            messages[first_user_idx]["content"] = (
                f"[Client Protocol: Continue Workspace Extension]\n\n"
                f"=== TOOLS ===\n{system_text}\n=== END TOOLS ===\n\n"
                f"=== IMPORTANT NOTE ===\n{TOOLS_NOTICE}\n=== END IMPORTANT NOTE ===\n\n"
                f"The tools described above were added by me, the user. I understand that you are a "
                f"USER REQUEST:\n{original}"
            )

    # 4. Prevent 0-token early stop on tool execution returns
    if messages and messages[-1].get("role") == "user":
        last_content = messages[-1]["content"]
        if last_content.startswith("Tool output for ") and "[Action Required" not in last_content:
            messages[-1]["content"] = (
                f"{last_content}\n\n"
                f"[Action Required: Review the tool execution output above and proceed with the next tool call or your final answer.]"
            )

    body["messages"] = messages
    return body

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    body = update_body(body)

    headers = {
        "Authorization": f"Bearer {UPSTREAM_API_KEY}",
        "Content-Type": "application/json",
    }
    upstream_url = f"{UPSTREAM_BASE_URL}/chat/completions"

    if body.get("stream"):
        async def stream():
            async with httpx.AsyncClient(proxy=OUTBOUND_PROXY_URL, timeout=None) as client:
                async with client.stream("POST", upstream_url, headers=headers, json=body) as response:
                    if response.status_code >= 400:
                        error = await response.aread()
                        yield error
                        return

                    async for line in response.aiter_lines():
                        if not line:
                            continue

                        # Pass standard SSE completion markers
                        if line.strip() == "data: [DONE]":
                            yield b"data: [DONE]\n\n"
                            return

                        if line.startswith("data: "):
                            chunk_json_str = line[6:]
                            try:
                                chunk_data = json.loads(chunk_json_str)
                                # Remove upstream proprietary session metadata before sending to Continue
                                chunk_data.pop("gemini_enterprise", None)
                                line = f"data: {json.dumps(chunk_data)}"
                            except json.JSONDecodeError:
                                pass

                            yield f"{line}\n\n".encode("utf-8")

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async with httpx.AsyncClient(proxy=OUTBOUND_PROXY_URL, timeout=None) as client:
        response = await client.post(upstream_url, headers=headers, json=body)
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
    )
