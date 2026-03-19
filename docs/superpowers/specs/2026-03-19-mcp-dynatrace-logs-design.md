# MCP Dynatrace Logs — Design Spec

**Date:** 2026-03-19
**Status:** Approved

## Overview

An MCP server that exposes Dynatrace log querying capabilities to LLMs via two tools. The server authenticates with the Dynatrace API using environment variables and supports DQL (Dynatrace Query Language) queries with an optional time range helper.

## Technology

- **Language:** Python
- **Package manager:** uv
- **MCP SDK:** `mcp[cli]`
- **HTTP client:** `httpx` (async)
- **Dev helper:** `python-dotenv`

## Credentials

Loaded from environment variables at server startup. If either is missing, the server fails immediately with a descriptive error message.

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

### POST body structure

```json
{
  "query": "fetch logs | filter context=\"*xxx*\" | limit 100",
  "defaultTimeframeStart": "now()-3d"
}
```

The `defaultTimeframeStart` key is only included when the `timeframe` parameter is provided. Its value is constructed as `now()-{timeframe}` (e.g. `timeframe=3d` → `now()-3d`).

## Tools

### `fetch_logs`

Submits a DQL query and polls until results are ready or the timeout is reached.

**Parameters:**
- `query` (str, required) — full DQL string, e.g. `fetch logs | filter context="*xxx*" | limit 100`
- `timeframe` (str, optional) — time offset, e.g. `3d`, `1h`. Injected as `defaultTimeframeStart: now()-{timeframe}` in the POST body.
- `max_wait_seconds` (int, optional, default 30) — polling timeout in seconds

**Behavior:**
1. POST to `query:execute` with the DQL query (and `defaultTimeframeStart` if `timeframe` is set)
2. Poll `query:poll` every 1 second
3. Stop polling when state is `SUCCEEDED`, `FAILED`, or `max_wait_seconds` is reached
4. On `FAILED`: return a structured error (see Error Handling)
5. On timeout: return state `TIMEOUT` with the `request_token` for manual follow-up

**Returns (SUCCEEDED):**
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

**Returns (TIMEOUT):**
```json
{
  "state": "TIMEOUT",
  "message": "Query did not complete within 30 seconds. Use poll_query with the request_token to retrieve results.",
  "metadata": {
    "request_token": "abc=="
  }
}
```

**Returns (FAILED):**
```json
{
  "state": "FAILED",
  "error": "<error message from Dynatrace response>",
  "metadata": {
    "request_token": "abc=="
  }
}
```

### `poll_query`

Manual poll for an in-progress or completed query.

**Parameters:**
- `request_token` (str, required)

**Returns (RUNNING):**
```json
{
  "state": "RUNNING",
  "progress": 60,
  "metadata": {
    "request_token": "abc=="
  }
}
```

**Returns (SUCCEEDED):**
```json
{
  "state": "SUCCEEDED",
  "progress": 100,
  "records": [...],
  "metadata": {
    "request_token": "abc=="
  }
}
```

**Returns (FAILED):**
```json
{
  "state": "FAILED",
  "error": "<error message from Dynatrace response>",
  "metadata": {
    "request_token": "abc=="
  }
}
```

Notes:
- `progress` is an integer from 0 to 100 representing percent complete; may be absent if the API does not provide it
- `records` is present and non-empty only when `state == SUCCEEDED`
- `metadata.request_token` is always included to allow chaining calls

## Error Handling

| Scenario | Behavior |
|---|---|
| Missing env vars | Server startup failure with descriptive message |
| HTTP 4xx (bad token, invalid DQL) | Return structured error with `state: "ERROR"`, `status_code`, and Dynatrace response body |
| HTTP 5xx | Return structured error with `state: "ERROR"`, `status_code`, and Dynatrace response body |
| Poll returns `state: FAILED` | Return `state: "FAILED"` with `error` field containing Dynatrace's error message |
| Polling timeout (`max_wait_seconds`) | Return `state: "TIMEOUT"` with `request_token` for manual follow-up |
| Network/connection error (httpx) | Return structured error with `state: "ERROR"` and the exception message |

All errors are returned as structured dicts (not raised as exceptions) so the LLM receives actionable information.

## Limits

- Default query limit: 100 records (set in DQL by the LLM, not enforced server-side)
- No server-side hard cap — the LLM controls the limit via the DQL `limit` clause
- `max_wait_seconds` defaults to 30s to avoid blocking indefinitely
