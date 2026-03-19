# MCP Dynatrace Logs — Design Spec

**Date:** 2026-03-19
**Status:** Approved

## Overview

An MCP server that exposes Dynatrace log querying capabilities to LLMs via two tools. The server authenticates with the Dynatrace API using environment variables and supports DQL (Dynatrace Query Language) queries with optional time range helpers.

## Technology

- **Language:** Python
- **Package manager:** uv
- **MCP SDK:** `mcp[cli]`
- **HTTP client:** `httpx` (async)
- **Dev helper:** `python-dotenv`

## Credentials

Loaded from environment variables at server startup. If either is missing, the server fails immediately with a clear error.

- `DYNATRACE_URL` — base URL, e.g. `https://your-tenant.apps.dynatrace.com`
- `DYNATRACE_API_TOKEN` — Bearer token

## Project Structure

```
mcp-dynatrace-logs/
├── src/
│   └── mcp_dynatrace_logs/
│       ├── __init__.py
│       ├── server.py        # MCP entrypoint, registers tools
│       ├── client.py        # httpx async client for Dynatrace API
│       └── tools.py         # fetch_logs and poll_query implementations
├── pyproject.toml
└── README.md
```

## API Endpoints

- **POST** `{DYNATRACE_URL}/platform/storage/query/v1/query:execute` — submit a DQL query, returns a `request-token`
- **GET** `{DYNATRACE_URL}/platform/storage/query/v1/query:poll?request-token=<token>` — poll for results

## Tools

### `fetch_logs`

Submits a DQL query and polls until results are ready or timeout is reached.

**Parameters:**
- `query` (str, required) — full DQL string, e.g. `fetch logs | filter context="*xxx*" | limit 100`
- `timeframe` (str, optional) — time offset prepended to the query, e.g. `-3d`, `-1h`. Injected as `from: now()-3d` in the request body.
- `max_wait_seconds` (int, optional, default 30) — polling timeout

**Behavior:**
1. POST to `query:execute` with the DQL query
2. Poll `query:poll` every 1 second until state is `SUCCEEDED`, `FAILED`, or timeout
3. On timeout: return the `request_token` so the LLM can continue with `poll_query`

**Returns:**
```json
{
  "state": "SUCCEEDED",
  "records": [...],
  "metadata": {
    "total": 42,
    "returned": 42,
    "request_token": "abc=="
  }
}
```

### `poll_query`

Manual poll for an in-progress query.

**Parameters:**
- `request_token` (str, required)

**Returns:**
```json
{
  "state": "RUNNING | SUCCEEDED | FAILED",
  "progress": 60,
  "records": [...],
  "request_token": "abc=="
}
```

## Error Handling

| Scenario | Behavior |
|---|---|
| Missing env vars | Server startup failure with descriptive message |
| HTTP 4xx (bad token, invalid DQL) | Return error with status code + Dynatrace response body |
| HTTP 5xx | Relay Dynatrace error message |
| Polling timeout | Return `request_token` for manual follow-up |

## Limits

- Default query limit: 100 records (set in DQL, not enforced by server)
- No server-side hard cap — the LLM controls limit via DQL
- `max_wait_seconds` defaults to 30s to avoid blocking indefinitely

## Notes

- The server does not construct DQL — the LLM writes the full query string
- `timeframe` is the only helper parameter that modifies the request before sending
- `request_token` is always included in responses to enable pagination/re-polling
