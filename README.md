# mcp-dynatrace-logs

MCP server for querying Dynatrace logs via DQL.

## Setup

### 1. Install

```bash
uv sync
```

### 2. Configure credentials

Copy `.env.example` to `.env` and fill in your values:

```
DYNATRACE_URL=https://your-tenant.apps.dynatrace.com
DYNATRACE_API_TOKEN=dt0s16.XXXX.YYYY
```

### 3. Run (dev)

```bash
uv run mcp-dynatrace-logs
```

### 4. Claude Desktop integration

Add to `~/.config/claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dynatrace-logs": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-dynatrace-logs", "mcp-dynatrace-logs"],
      "env": {
        "DYNATRACE_URL": "https://your-tenant.apps.dynatrace.com",
        "DYNATRACE_API_TOKEN": "dt0s16.XXXX.YYYY"
      }
    }
  }
}
```

### 5. Claude Code (CLI) integration

```bash
claude mcp add dynatrace-logs \
  --env DYNATRACE_URL=https://your-tenant.apps.dynatrace.com \
  --env DYNATRACE_API_TOKEN=dt0s16.XXXX.YYYY \
  -- uv run --directory /path/to/mcp-dynatrace-logs mcp-dynatrace-logs
```

Verify the server is registered:

```bash
claude mcp list
```

### 6. GitHub Copilot (VS Code) integration

Create or edit `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "dynatrace-logs": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-dynatrace-logs", "mcp-dynatrace-logs"],
      "env": {
        "DYNATRACE_URL": "https://your-tenant.apps.dynatrace.com",
        "DYNATRACE_API_TOKEN": "dt0s16.XXXX.YYYY"
      }
    }
  }
}
```

Requires VS Code 1.99+ with GitHub Copilot extension. Enable MCP support via `"chat.mcp.enabled": true` in VS Code settings if not already active.

## Tools

### `fetch_logs`

Executes a DQL query and polls until results are ready.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | str | yes | — | Full DQL string |
| `timeframe` | str | no | — | Time offset: `3d`, `1h`, `30m` |
| `max_wait_seconds` | int | no | 30 | Polling timeout |

**Example queries:**

```
fetch logs | limit 100
fetch logs | filter contains(content, "my-service") | limit 50
fetch logs, from:now()-24h | filter contains(content, "retry-stuck") | fields timestamp, content | limit 100
fetch logs, from:now()-3d | filter status="ERROR" | fields timestamp, content | limit 200
```

> **Tip:** Use `from:now()-Xh` / `from:now()-Xd` directly in the DQL query to control the search
> window. This is more reliable than the `timeframe` parameter.

**Response states:** `SUCCEEDED`, `FAILED`, `TIMEOUT`, `ERROR`

On `TIMEOUT`, use `poll_query` with the returned `request_token` to retrieve results.

### `poll_query`

Manually poll a query using a `request_token` from a previous call.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `request_token` | str | yes | Token from `fetch_logs` or `poll_query` |

**Response states:** `RUNNING`, `SUCCEEDED`, `FAILED`, `ERROR`

## Running tests

```bash
uv run pytest -v
```
