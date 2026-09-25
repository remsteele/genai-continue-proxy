import json
import os
import re
from typing import Any
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

app = FastAPI()

UPSTREAM_BASE_URL = os.environ.get(
    "UPSTREAM_BASE_URL",
    "https://api.genai.mil/v1",
).rstrip("/")

UPSTREAM_API_KEY = os.environ["UPSTREAM_API_KEY"]
OUTBOUND_PROXY_URL = os.environ.get("OUTBOUND_PROXY_URL")

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

    # Extract system prompt if present
    system_text = ""
    if messages[0].get("role") == "system":
        system_msg = messages.pop(0)
        system_text = content_to_text(system_msg.get("content", ""))

    for msg in messages:
        text = content_to_text(msg.get("content", ""))
        msg["content"] = sanitize_content(text)

    # Inject system instructions into the first user message
    first_user_idx = next((i for i, m in enumerate(messages) if m.get("role") == "user"), None)
    if first_user_idx is not None and system_text:
        original = messages[first_user_idx]["content"]
        # Only inject if not already injected
        if "[Client Protocol: Continue Workspace Extension]" not in original:
            messages[first_user_idx]["content"] = (
                f"[Client Protocol: Continue Workspace Extension]\n\n"
                f"=== TOOLS ===\n{system_text}\n=== END TOOLS ===\n\n"
                f"USER REQUEST:\n{original}"
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
            total_chunks = 0
            has_yielded_text = False
            async with httpx.AsyncClient(proxy=OUTBOUND_PROXY_URL, timeout=None) as client:
                async with client.stream("POST", upstream_url, headers=headers, json=body) as response:
                    if response.status_code >= 400:
                        error = await response.aread()
                        yield error
                        return

                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        if line.strip() == "data: [DONE]":
                            # If upstream completed without sending any content, abort loop
                            if not has_yielded_text:
                                error_chunk = {
                                    "choices": [{
                                        "delta": {"content": "\n⚠️ Upstream generated 0 tokens (possible safety filter or context limit hit). Halting loop."},
                                        "finish_reason": "stop"
                                    }]
                                }
                                yield f"data: {json.dumps(error_chunk)}\n\n".encode("utf-8")
                            yield b"data: [DONE]\n\n"
                            return

                        chunk_json_str = line[6:]
                        try:
                            chunk_data = json.loads(chunk_json_str)
                            delta = chunk_data.get("choices", [{}])[0].get("delta", {})
                            if delta.get("content"):
                                has_yielded_text = True
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
