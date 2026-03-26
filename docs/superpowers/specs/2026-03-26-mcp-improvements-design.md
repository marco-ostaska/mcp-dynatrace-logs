# MCP Dynatrace Logs — Improvements Design

**Date:** 2026-03-26
**Status:** Approved

## Context

`mcp-dynatrace-logs` is a FastMCP server that lets Claude/Copilot query Dynatrace logs via DQL. The current implementation works but has four problems:

1. Incorrect DQL syntax documented in docstrings causes the LLM to generate invalid queries (parse errors on `sort`)
2. The 30s polling timeout is too short for complex queries; when TIMEOUT occurs, Claude/Copilot does not retry automatically
3. Communication errors return opaque technical messages with no actionable diagnosis
4. Queries return raw `content` instead of the structured `Log message` field that Dynatrace UI extracts automatically

## Goals

- Claude/Copilot generates syntactically correct DQL queries on the first try
- Complex queries complete successfully instead of silently timing out
- Error messages tell Claude exactly what is wrong and what to do
- Results include a readable `Log message` field for use in reports and analysis

## Design

### 1. DQL Documentation Fix (`server.py`)

The docstring for `fetch_logs` contains incorrect DQL examples:

- `| sort by timestamp desc` → **wrong** (Dynatrace DQL does not use `by`)
- Correct syntax: `| sort timestamp desc`

Fix the docstring to show correct syntax. Add a realistic multi-field example that matches what Dynatrace UI generates. No runtime transformation of queries — the LLM must generate correct syntax from correct documentation.

### 2. Robust Polling with Retry (`tools.py`)

**Timeout:** Increase `max_wait_seconds` default from 30 to 120 seconds.

**Network retry:** Distinguish transient errors (network timeout, connection reset) from permanent errors (4xx HTTP). On transient errors during polling, retry up to 3 times with 2s backoff before returning ERROR.

**TIMEOUT message:** When the loop exhausts `max_wait_seconds`, return a `message` that explicitly instructs Claude to call `poll_query` immediately with the provided `request_token`, without waiting for user input.

**Progress visibility:** When a poll response includes a `progress` field, include it in RUNNING/TIMEOUT responses so Claude can report query advancement to the user.

### 3. Actionable Error Messages (`tools.py`, `client.py`)

Replace generic httpx exception strings with context-aware messages:

| Condition | Message |
|---|---|
| HTTP 401 | `"API token inválido ou expirado. Verifique DYNATRACE_API_TOKEN."` |
| HTTP 403 | `"Token sem permissão de leitura de logs. Verifique os escopos do token no Dynatrace."` |
| HTTP 400 (query error) | API message + `" — verifique a sintaxe DQL."` |
| Connection error / timeout | `"Não foi possível conectar ao Dynatrace em <URL>. Verifique DYNATRACE_URL e conectividade."` |
| Missing env vars | Already handled in `_build_client()` — no change needed |

The Dynatrace base URL is included in connection error messages. The token is never included.

### 4. Automatic Query Enrichment (`tools.py`)

**Condition:** Applied only when the query string does not contain `fieldsAdd` (case-sensitive substring check). Queries already using `fieldsAdd` pass through unchanged.

**What is injected:** The standard Dynatrace log enrichment block that extracts a human-readable `Log message` field from JSON payloads, key=value patterns, and common message fields (`msg`, `message`, `event`, `description`, `details`). This is exactly the block Dynatrace UI injects automatically. Confirmed working via the API (returns `RUNNING`, no parse error).

**Injection point:** The enrichment block is inserted after all `filter` and `fieldsAdd`-free operations, and **before** any trailing `| sort` or `| limit` clause. This preserves Dynatrace's expected pipeline order.

**Result:** The `records` returned to Claude include a `Log message` field alongside `timestamp`, `severity`, and `content`, making it straightforward to generate readable reports without post-processing.

**Enrichment block (injected verbatim):**

```dql
| fieldsAdd __attributes_array = array(msg,message,event,description,details)
| fieldsAdd __log_message_attr = arrayFirst(iCollectArray(if(__attributes_array[]!="", __attributes_array[])))
| parse content, "JSON:'__parsed_json'", parsingPrerequisite: isNull(__log_message_attr) and startsWith(content, "{")
| fieldsAdd __json_fields_array = array(__parsed_json[`message`],__parsed_json[`@message`],__parsed_json[`msg`],__parsed_json[`@mt`],__parsed_json[`@m`],__parsed_json[`body`],__parsed_json[`eventName`],__parsed_json[`textPayload`][`message`],__parsed_json[`textPayload`],__parsed_json[`protoPayload`][`@type`],__parsed_json[`protoPayload`][`message`],__parsed_json[`jsonPayload`][`message`],__parsed_json[`messageObject`][`message`],__parsed_json[`properties`][`message`],__parsed_json[`properties`][`statusMessage`],__parsed_json[`properties`][`status`][`additionalDetails`],__parsed_json[`properties`][`log`],__parsed_json[`properties`][`Log`],__parsed_json[`properties`][`Result`],__parsed_json[`content`][`detail`][`event`],__parsed_json[`Body`][`Value`])
| fieldsAdd `Log message` = toString(coalesce(__log_message_attr,arrayFirst(iCollectArray(if(__json_fields_array[]!="", __json_fields_array[])))))
| parse coalesce(`Log message`, content), "(DATA (' '|SPACE))? ('msg'|'message'|'Message') '=' DQS:'__log_message_kv'", parsingPrerequisite: matchesValue(coalesce(`Log message`, content), {"*msg=*","*message=*","*Message=*"}, caseSensitive:true)
| fieldsAdd `Log message` = coalesce(__log_message_kv, `Log message`)
| fieldsRemove __parsed_json, __log_message_attr, __log_message_kv, __attributes_array, __json_fields_array
```

## Files Changed

| File | Change |
|---|---|
| `src/mcp_dynatrace_logs/server.py` | Fix DQL docstring examples |
| `src/mcp_dynatrace_logs/tools.py` | Polling retry, timeout default, enrichment injection, error messages |
| `src/mcp_dynatrace_logs/client.py` | Actionable error messages per HTTP status |

## Out of Scope

- No new MCP tools
- No changes to `poll_query` enrichment (it returns raw poll data by design)
- No changes to authentication mechanism
