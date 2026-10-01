# Continue Proxy

A small local FastAPI proxy that adapts Continue's OpenAI-compatible chat-completions requests for genai.mil.

## Quick Start

Create a virtual environment, install the dependencies, and create your private configuration file:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`, then start the proxy:

```bash
make run
```

The proxy listens only on `127.0.0.1:8000` and exposes `POST http://127.0.0.1:8000/v1/chat/completions`.

Point Continue (or another OpenAI-compatible client) at `http://127.0.0.1:8000/v1`.

## How to use with GenAI.mil + Continue

Set these values in `.env`:

```bash
export UPSTREAM_BASE_URL="https://api.genai.mil/v1"
export UPSTREAM_API_KEY="<key>"
export OUTBOUND_PROXY_URL="socks5://127.0.0.1:8896"
```

Replace `<key>` with your genai.mil API key. Keep the other required values from `.env.example`, including `REASONING_EFFORT`.

Start things in this order:

1. Run the SOCKS proxy: `niera socks dev26`
2. Start this proxy: `make run`
3. Send a message in Continue.

### Continue settings

You'll need to have the `Continue - open-source AI code agent` VS Code extension installed.

Click the gear icon to open Settings.

- Under **Chat**, disable **Enable Session Titles**.
- Under **Experimental**, enable **Enable experimental tools** and **Only use system message tools**. Leave every other experimental setting disabled.

Then, click the Tools menu on the left.

- Exclude `run_terminal_command`, `view_diff`, `read_currently_open_file`, `create_rule_block`, and `request_rule`.

Then click **Configs** and use [continue-genai-mil.yaml](continue-genai-mil.yaml) as your Main Config. It sends requests to the local proxy, not directly to genai.mil. Select **Gemini 3.8 Flash** in Continue.
