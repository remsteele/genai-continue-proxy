import os
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
OUTBOUND_PROXY_URL = os.environ["OUTBOUND_PROXY_URL"]


def content_to_text(content: Any) -> str:
    """
    Convert OpenAI-style message content into plain text.
    Continue's system prompt is normally already a string, but
    this also handles text-content arrays.
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []

        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif "text" in item:
                    parts.append(str(item["text"]))

        return "\n".join(parts)

    return str(content)


def rewrite_messages(messages: list[dict]) -> list[dict]:
    """
    Remove system-role messages and prepend their contents
    to the first user message.

    This preserves Continue's system-message tool instructions
    while avoiding the upstream gateway's system-role handling.
    """

    system_parts = []
    remaining = []

    for message in messages:
        if message.get("role") == "system":
            system_parts.append(content_to_text(message.get("content", "")))
        else:
            remaining.append(message)

    if not system_parts:
        return remaining

    system_text = "\n\n".join(system_parts)

    injected = f"""<client_system_instructions>

IMPORTANT: You are running inside the Continue coding agent.

Continue provides tools that allow you to inspect, read, search, and edit
files in the user's workspace. You DO have access to the user's workspace 
through these tools.

STRICT OPERATIONAL RULES:
1. DO NOT say that you cannot access local files.
2. DO NOT ask the user to paste code or attach files when a workspace tool can retrieve them.
3. DO NOT merely describe, narrate, or promise a tool action (e.g., never say "I will now read the file").
4. TERMINAL RESTRICTION (CRITICAL): You are in a remote WSL environment where terminal stdout capture is disabled. 
   - NEVER call terminal or bash commands (e.g., do not attempt to run 'ls', 'cat', 'grep', 'find', or run shell scripts).
   - ONLY use file-system and workspace tools (such as tools to read files, list directories, write files, or edit code).
5. Always rely on reading and modifying project files directly on disk.

The following are the complete tool instructions supplied by Continue.
Follow the tool definitions and tool-call syntax in these instructions exactly:

{system_text}

</client_system_instructions>

IMPORTANT TOOL-USAGE PROTOCOL:

When the current task requires inspecting or modifying the workspace, CALL 
THE APPROPRIATE FILE TOOL immediately.

Do not write conversational filler like:
"I will read the file."
"I am going to check your workspace."
"I cannot see your files."

Instead, emit the tool call using EXACTLY the XML/text syntax specified in the 
Continue instructions above.

Rule for output: Emit only one tool call at a time. The tool call MUST be 
the final element in your response so the client parser can execute it.

CURRENT USER REQUEST:

"""

    for message in reversed(remaining):
        if message.get("role") == "user":
            original = message.get("content", "")

            if isinstance(original, str):
                message["content"] = injected + original
            else:
                message["content"] = [
                    {
                        "type": "text",
                        "text": injected,
                    },
                    *original,
                ]

            return remaining


    # Unusual case: request contains a system message but no user message.
    remaining.insert(
        0,
        {
            "role": "user",
            "content": injected,
        },
    )

    return remaining


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()

    body["messages"] = rewrite_messages(body.get("messages", []))

    headers = {
        "Authorization": f"Bearer {UPSTREAM_API_KEY}",
        "Content-Type": "application/json",
    }

    upstream_url = f"{UPSTREAM_BASE_URL}/chat/completions"

    if body.get("stream"):
        async def stream():
            async with httpx.AsyncClient(
                proxy=OUTBOUND_PROXY_URL,
                timeout=None,
            ) as client:
                async with client.stream(
                    "POST",
                    upstream_url,
                    headers=headers,
                    json=body,
                ) as response:

                    if response.status_code >= 400:
                        error = await response.aread()
                        yield error
                        return

                    async for chunk in response.aiter_raw():
                        yield chunk

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    async with httpx.AsyncClient(
                proxy=OUTBOUND_PROXY_URL,
                timeout=None,
            ) as client:
        response = await client.post(
            upstream_url,
            headers=headers,
            json=body,
        )

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get(
            "content-type",
            "application/json",
        ),
    )