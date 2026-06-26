# Link Finder MCP

An [MCP](https://modelcontextprotocol.io) server for the [Link Finder API](https://app.link-finder.net) — find backlink opportunities, analyze competitors, discover similar domains with AI embeddings, and manage prospecting projects directly from Claude, ChatGPT, Cursor, or any MCP client.

Secrets are provided only through environment variables, and the server is host-agnostic: run it locally over stdio, or deploy it anywhere (Render or any VM) with a bearer-token-protected HTTP/SSE endpoint.

---

## Features

All Link Finder API v2 endpoints are exposed as tools:

| Tool | Endpoint | Plan | What it does |
| --- | --- | --- | --- |
| `get_account` | `getAccount` | Booster | Plan, remaining credits, available features |
| `list_platforms` | `listPlatforms` | Booster | Supported netlinking platforms |
| `list_locations` | `listLocations` | Booster | Countries/locations for keyword search |
| `keyword_search` | `kwSearch` | Booster | Find opportunities from keywords (SERP analysis) |
| `competitor_analysis` | `competitor` | Booster | A competitor's available referring domains |
| `ai_search` | `aiSearch` | Booster | AI prospecting with relevance scoring |
| `similar_domains` | `similarDomains` | Booster | AI-embedding lookalike domains (the gem finder) |
| `create_project` | `createProject` | Booster | Create a project |
| `list_projects` | `listProjects` | Booster | List projects with counts |
| `project_favorites` | `projectFavorites` | Booster | Favorites in a project with full metrics |
| `add_favorite` | `addFavorite` | Booster | Add / remove a domain from a project |
| `update_note` | `updateNote` | Booster | Annotate a standout favorite |
| `check_domain` | `checkDomain` | API | Check one domain across all platforms |
| `bulk_check` | `bulk` | API | Check up to 50,000 domains at once |
| `get_search_history` | _local_ | — | Read locally saved search history |

Plus a guided **prompt** `backlink_workflow` that runs the step-by-step interview and prospecting flow.

The server also follows the API's best practices: every search result is **saved locally** to a `data/` folder and logged in `data/searchHistory.json` so agents can avoid duplicate, credit-wasting searches.

---

## Requirements

- Python 3.10+
- A Link Finder API key — get it in your account at <https://app.link-finder.net/account/> (Booster plan or higher; `checkDomain` and `bulk` need the API plan)

---

## Installation

```bash
git clone https://github.com/<you>/link-finder-mcp.git
cd link-finder-mcp

python -m venv .venv && source .venv/bin/activate    # optional but recommended
pip install -r requirements.txt
```

Copy the example environment file and fill in your key:

```bash
cp .env.example .env
# then edit .env and set LINK_FINDER_API_KEY
```

> **No credentials in code.** The API key is read only from `LINK_FINDER_API_KEY` and is never accepted as a tool argument, so it can't leak through the model context.

---

## Configuration

All configuration is via environment variables:

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `LINK_FINDER_API_KEY` | yes | — | Your Link Finder API key |
| `MCP_TRANSPORT` | no | `stdio` | `stdio` (local), `sse` or `http` (hosted) |
| `MCP_BEARER_TOKEN` | hosted only | — | Shared secret clients send as `Authorization: Bearer <token>` |
| `PORT` | no | `8000` | Port to bind in hosted mode (Render/Railway/Fly inject this) |
| `HOST` | no | `0.0.0.0` | Bind address in hosted mode |
| `LINK_FINDER_DATA_DIR` | no | `data` | Where results + history are saved (empty = disable) |
| `LINK_FINDER_BASE_URL` | no | `https://app.link-finder.net/api/v2` | Override the API base URL |
| `LINK_FINDER_HTTP_TIMEOUT` | no | `120` | HTTP timeout in seconds |
| `MCP_ALLOWED_HOSTS` | no | _(empty)_ | Comma-separated Host allowlist for DNS-rebinding protection. Empty = disabled (works behind any proxy). Supports a `:*` port wildcard. |
| `MCP_ALLOWED_ORIGINS` | no | _(empty)_ | Comma-separated Origin allowlist (used with the above). |

---

## Running locally (stdio)

```bash
export PYTHONPATH=src
python -m link_finder_mcp.server
```

Or debug interactively with the MCP Inspector:

```bash
PYTHONPATH=src mcp dev src/link_finder_mcp/server.py
```

---

## Use with Claude Desktop

Edit your Claude config:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "link-finder": {
      "command": "python",
      "args": ["-m", "link_finder_mcp.server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/link-finder-mcp/src",
        "MCP_TRANSPORT": "stdio",
        "LINK_FINDER_API_KEY": "your_link_finder_api_key_here",
        "LINK_FINDER_DATA_DIR": "/absolute/path/to/link-finder-mcp/data"
      }
    }
  }
}
```

Restart Claude Desktop. You'll see the Link Finder tools under the tools (hammer) icon. Try:

> "Check my Link Finder credits, then find French backlink opportunities for the keywords `assurance auto;comparateur assurance` with DR 20+ and 500+ traffic. Save the best ones to a new project called *Assurance Q3*."

Claude will chain `get_account` → `keyword_search` → `create_project` → `add_favorite`, then suggest `similar_domains` on the top matches.

> Tip: in Claude Desktop you can also attach the **`backlink_workflow`** prompt (the "+" / prompts menu) to launch the full guided interview.

---

## Use with ChatGPT

ChatGPT supports remote MCP servers (Developer mode / custom connectors and the Responses API `tools` of type `mcp`). For that you need the server reachable over HTTPS with a bearer token — see [Deploy on Render](#deploy-on-render-or-any-vm).

### Option A — ChatGPT Developer Mode / Connectors (UI)

1. Deploy the server (e.g. on Render) with `MCP_TRANSPORT=sse` and a strong `MCP_BEARER_TOKEN`.
2. In ChatGPT: **Settings → Connectors → Advanced → Developer mode**, then **Create** a connector.
3. Set the server URL to your deployment's SSE endpoint, e.g. `https://your-app.onrender.com/sse`.
4. Add an `Authorization` header: `Bearer <your MCP_BEARER_TOKEN>`.
5. Save, then enable the connector in a chat and ask it to find backlinks.

### Option B — OpenAI Responses API (programmatic)

```python
from openai import OpenAI

client = OpenAI()

resp = client.responses.create(
    model="gpt-4.1",
    tools=[
        {
            "type": "mcp",
            "server_label": "link-finder",
            "server_url": "https://your-app.onrender.com/sse",
            "headers": {"Authorization": "Bearer YOUR_MCP_BEARER_TOKEN"},
            "require_approval": "never",
        }
    ],
    input="Use Link Finder to find Spanish (language 2724) backlink "
          "opportunities for 'hosting wordpress' with TF 15+ and report a table.",
)

print(resp.output_text)
```

---

## Use with Cursor

Add to `~/.cursor/mcp.json` (or the project `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "link-finder": {
      "command": "python",
      "args": ["-m", "link_finder_mcp.server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/link-finder-mcp/src",
        "LINK_FINDER_API_KEY": "your_link_finder_api_key_here"
      }
    }
  }
}
```

---

## Deploy on Render (or any VM)

The server is host-agnostic. In hosted mode it binds `0.0.0.0:$PORT` and protects the MCP endpoints with a bearer token.

A ready-made [`render.yaml`](./render.yaml) is included:

```yaml
services:
  - type: web
    name: link-finder-mcp
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: python -m link_finder_mcp.server
    envVars:
      - key: PYTHONPATH
        value: src
      - key: MCP_TRANSPORT
        value: sse
      - key: LINK_FINDER_API_KEY
        sync: false
      - key: MCP_BEARER_TOKEN
        sync: false
```

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, point it at the repo.
3. Set the two secret env vars (`LINK_FINDER_API_KEY`, `MCP_BEARER_TOKEN`) in the dashboard.
4. Deploy. Your SSE endpoint will be `https://<service>.onrender.com/sse`.

The same works on any VM / PaaS — just set the env vars and run `python -m link_finder_mcp.server`. Use `MCP_TRANSPORT=http` instead of `sse` for the Streamable HTTP transport.

> **Note on saved data:** on ephemeral hosts (like Render's default disk) the `data/` folder is not persistent. Mount a persistent disk, or set `LINK_FINDER_DATA_DIR` to a mounted path, if you want the search history to survive restarts. Local (stdio) usage persists normally.

### Troubleshooting

- **`SSE error: Non-200 status code (421)` / `Invalid Host header`** — this is DNS-rebinding protection rejecting the proxy's public hostname. The server disables host checking by default (the bearer token already guards it), so a fresh deploy works out of the box. If you set `MCP_ALLOWED_HOSTS`, make sure it includes your public host, e.g. `your-app.onrender.com`.
- **`GET / → 404` / `POST /sse → 405` in the logs** — harmless. The SSE transport serves a stream on `GET /sse` and accepts messages on `POST /messages/`; probes hitting other paths/methods are expected. Point your client at the `/sse` path.

---

## How credits work

- Credits are shared across the web app, browser extension, and API.
- `keyword_search` costs 1 `keywords_search` credit **per keyword**; `competitor_analysis` 1 per request; `ai_search` 1 per request; `similar_domains` 1 per domain (or per project search).
- Credits are only consumed when results are found.
- Always call `get_account` first to check remaining credits and which features your plan unlocks.

## Reading results

Each domain result includes fields you can filter and sort on: `title`, `domain`, `dr` (Ahrefs), `tf`/`cf` (Majestic), `rd`, `traffic`, `ttf0` (topic), `ai_lang`, `gg_news`, and per-platform prices (`-2` = not found, `-1` = price unavailable, `>0` = price in the chosen currency). Each platform also has a `_url` field with the direct purchase link, and `best_price_platform` names the cheapest one.

---

## License

MIT — see [LICENSE](./LICENSE).
