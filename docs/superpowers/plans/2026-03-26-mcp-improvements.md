# MCP Dynatrace Logs Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix DQL sort documentation, make polling resilient with retry and longer timeout, add actionable error messages, and auto-enrich queries with Dynatrace's `Log message` extraction block.

**Architecture:** All changes are confined to three existing files: `server.py` (docstring fix), `tools.py` (polling logic + enrichment), and `client.py` (error messages). No new files, no new MCP tools, no schema changes.

**Tech Stack:** Python 3.11+, FastMCP, httpx, pytest + pytest-asyncio (asyncio_mode=auto), unittest.mock.AsyncMock

---

## Task 1: Fix DQL sort documentation in server.py

**Files:**
- Modify: `src/mcp_dynatrace_logs/server.py`

No test needed — this is a docstring-only change. Verify visually.

- [ ] **Step 1: Open and read the current docstring**

Read `src/mcp_dynatrace_logs/server.py` lines 33–65. Note the incorrect example `| sort by timestamp desc`.

- [ ] **Step 2: Replace the fetch_logs docstring**

Replace the entire docstring of the `fetch_logs` MCP tool with:

```python
    """
    Execute a DQL query against Dynatrace logs and return results.

    Args:
        query: Full DQL string. DQL SYNTAX RULES:
               - Always start with: fetch logs
               - Pipe each operation: fetch logs | filter ... | sort ... | limit ...
               - Use contains(field, "value") for substring matching
               - Use matches(field, "pattern") for regex
               - Combine filters: | filter field1 == "value" AND field2 == "value"
               - Sort (NO 'by' keyword): | sort timestamp desc
               - Limit: | limit 100
               - Common fields: content, severity, status, timestamp

               Examples:
               - "fetch logs | filter contains(content, \"error\") | sort timestamp desc | limit 50"
               - "fetch logs | filter severity == \"ERROR\" AND contains(content, \"order-id\") | sort timestamp desc"

               NOTE: The server automatically enriches your query to extract a readable
               'Log message' field from JSON and key=value log formats, unless your query
               already contains 'fieldsAdd'.

        timeframe: Optional time offset like "3d", "1h", "30m". Adds defaultTimeframeStart.
        max_wait_seconds: How long to poll before returning TIMEOUT (default 120).

    Returns a dict with "state": SUCCEEDED, FAILED, TIMEOUT, or ERROR.
    On TIMEOUT, call poll_query immediately with the returned request_token.
    """
```

- [ ] **Step 3: Commit**

```bash
git add src/mcp_dynatrace_logs/server.py
git commit -m "docs: fix DQL sort syntax in fetch_logs docstring"
```

---

## Task 2: Actionable error messages in client.py

**Files:**
- Modify: `src/mcp_dynatrace_logs/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write failing tests for actionable errors**

Add to `tests/test_client.py`:

```python
@respx.mock
async def test_execute_401_raises_with_message(client):
    respx.post(f"{BASE_URL}/platform/storage/query/v1/query:execute").mock(
        return_value=httpx.Response(401, json={"error": {"message": "unauthorized"}})
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.execute("fetch logs")
    assert "token" in str(exc_info.value).lower() or exc_info.value.response.status_code == 401


@respx.mock
async def test_execute_403_raises_with_message(client):
    respx.post(f"{BASE_URL}/platform/storage/query/v1/query:execute").mock(
        return_value=httpx.Response(403, json={"error": {"message": "forbidden"}})
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.execute("fetch logs")
    assert exc_info.value.response.status_code == 403


@respx.mock
async def test_execute_connection_error_includes_url(client):
    respx.post(f"{BASE_URL}/platform/storage/query/v1/query:execute").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(httpx.ConnectError) as exc_info:
        await client.execute("fetch logs")
    assert BASE_URL in str(exc_info.value)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_client.py::test_execute_401_raises_with_message tests/test_client.py::test_execute_403_raises_with_message tests/test_client.py::test_execute_connection_error_includes_url -v
```

Expected: FAIL (currently no URL in ConnectError message).

- [ ] **Step 3: Add _raise_for_status helper to client.py**

Replace the entire content of `src/mcp_dynatrace_logs/client.py` with:

```python
import re
import httpx
from datetime import datetime, timezone, timedelta


def _timeframe_to_iso(timeframe: str) -> str:
    """Convert a relative timeframe like '1h', '3d', '30m' to an ISO 8601 UTC timestamp."""
    match = re.fullmatch(r"(\d+)([smhd])", timeframe)
    if not match:
        raise ValueError(
            f"Invalid timeframe {timeframe!r}. Expected <number><unit>, e.g. '1h', '3d', '30m'."
        )
    value, unit = int(match.group(1)), match.group(2)
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    delta = timedelta(**{units[unit]: value})
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raise_for_status(response: httpx.Response, base_url: str) -> None:
    """Raise HTTPStatusError with an actionable message based on status code."""
    if response.status_code == 401:
        raise httpx.HTTPStatusError(
            "API token inválido ou expirado. Verifique DYNATRACE_API_TOKEN.",
            request=response.request,
            response=response,
        )
    if response.status_code == 403:
        raise httpx.HTTPStatusError(
            "Token sem permissão de leitura de logs. Verifique os escopos do token no Dynatrace.",
            request=response.request,
            response=response,
        )
    if response.status_code == 400:
        try:
            api_msg = response.json().get("error", {}).get("message", response.text)
        except Exception:
            api_msg = response.text
        raise httpx.HTTPStatusError(
            f"{api_msg} — verifique a sintaxe DQL.",
            request=response.request,
            response=response,
        )
    response.raise_for_status()


class DynatraceClient:
    def __init__(self, base_url: str, token: str):
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def execute(self, query: str, timeframe: str | None = None) -> str:
        """POST query:execute. Returns the request token."""
        body: dict = {"query": query}
        if timeframe:
            body["defaultTimeframeStart"] = _timeframe_to_iso(timeframe)

        try:
            async with httpx.AsyncClient() as http:
                response = await http.post(
                    f"{self._base_url}/platform/storage/query/v1/query:execute",
                    headers=self._headers,
                    json=body,
                )
                _raise_for_status(response, self._base_url)
                return response.json()["requestToken"]
        except httpx.ConnectError as e:
            raise httpx.ConnectError(
                f"Não foi possível conectar ao Dynatrace em {self._base_url}. "
                f"Verifique DYNATRACE_URL e conectividade de rede. Detalhe: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise httpx.TimeoutException(
                f"Timeout ao conectar ao Dynatrace em {self._base_url}. "
                f"Verifique DYNATRACE_URL e conectividade de rede."
            ) from e

    async def poll(self, request_token: str) -> dict:
        """GET query:poll. Returns the full response JSON."""
        try:
            async with httpx.AsyncClient() as http:
                response = await http.get(
                    f"{self._base_url}/platform/storage/query/v1/query:poll",
                    headers=self._headers,
                    params={"request-token": request_token},
                )
                _raise_for_status(response, self._base_url)
                return response.json()
        except httpx.ConnectError as e:
            raise httpx.ConnectError(
                f"Não foi possível conectar ao Dynatrace em {self._base_url}. "
                f"Verifique DYNATRACE_URL e conectividade de rede. Detalhe: {e}"
            ) from e
        except httpx.TimeoutException as e:
            raise httpx.TimeoutException(
                f"Timeout ao conectar ao Dynatrace em {self._base_url}. "
                f"Verifique DYNATRACE_URL e conectividade de rede."
            ) from e
```

- [ ] **Step 4: Run the new tests**

```bash
uv run pytest tests/test_client.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dynatrace_logs/client.py tests/test_client.py
git commit -m "feat: add actionable error messages to DynatraceClient"
```

---

## Task 3: Robust polling with retry and longer timeout in tools.py

**Files:**
- Modify: `src/mcp_dynatrace_logs/tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write failing tests for retry and new timeout message**

Add to `tests/test_tools.py`:

```python
async def test_fetch_logs_default_timeout_is_120(mock_client):
    """Default max_wait_seconds must be 120."""
    import inspect
    from mcp_dynatrace_logs.tools import fetch_logs
    sig = inspect.signature(fetch_logs)
    assert sig.parameters["max_wait_seconds"].default == 120


async def test_fetch_logs_timeout_message_instructs_poll(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.return_value = {"state": "RUNNING", "progress": 10}
    result = await fetch_logs(mock_client, query="fetch logs", max_wait_seconds=0)
    assert result["state"] == "TIMEOUT"
    # Message must instruct Claude to call poll_query immediately
    assert "poll_query" in result["message"]
    assert "token==" in result["message"]


async def test_fetch_logs_poll_retries_on_network_error(mock_client):
    """On transient network error during polling, retries up to 3 times before ERROR."""
    mock_client.execute.return_value = "token=="
    mock_client.poll.side_effect = httpx.ConnectError("transient")
    result = await fetch_logs(mock_client, query="fetch logs", max_wait_seconds=10)
    # Should have retried 3 times total
    assert mock_client.poll.call_count == 3
    assert result["state"] == "ERROR"
    assert result["metadata"]["request_token"] == "token=="


async def test_fetch_logs_poll_succeeds_after_retry(mock_client):
    """If poll fails once but succeeds on retry, returns SUCCEEDED."""
    mock_client.execute.return_value = "token=="
    mock_client.poll.side_effect = [
        httpx.ConnectError("transient"),
        {"state": "SUCCEEDED", "records": [{"content": "ok"}]},
    ]
    result = await fetch_logs(mock_client, query="fetch logs", max_wait_seconds=10)
    assert result["state"] == "SUCCEEDED"
    assert result["records"] == [{"content": "ok"}]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_tools.py::test_fetch_logs_default_timeout_is_120 tests/test_tools.py::test_fetch_logs_timeout_message_instructs_poll tests/test_tools.py::test_fetch_logs_poll_retries_on_network_error tests/test_tools.py::test_fetch_logs_poll_succeeds_after_retry -v
```

Expected: FAIL.

- [ ] **Step 3: Rewrite fetch_logs in tools.py with retry logic**

Replace `fetch_logs` in `src/mcp_dynatrace_logs/tools.py` with:

```python
import asyncio
import httpx
from mcp_dynatrace_logs.client import DynatraceClient

_MAX_POLL_RETRIES = 3
_POLL_RETRY_BACKOFF = 2  # seconds


def _extract_error_message(e: httpx.HTTPStatusError) -> str:
    # Prefer the message baked into the exception by _raise_for_status
    if e.args and e.args[0] and not e.args[0].startswith("Client error"):
        return e.args[0]
    try:
        return e.response.json().get("error", {}).get("message", e.response.text)
    except Exception:
        return e.response.text


async def _poll_with_retry(client: DynatraceClient, request_token: str) -> dict | None:
    """Poll once, retrying up to _MAX_POLL_RETRIES times on transient network errors.

    Returns the poll response dict, or None if all retries are exhausted.
    Raises httpx.HTTPStatusError immediately (no retry) on 4xx/5xx responses.
    """
    last_error: Exception | None = None
    for attempt in range(_MAX_POLL_RETRIES):
        try:
            return await client.poll(request_token)
        except httpx.HTTPStatusError:
            raise  # permanent error, don't retry
        except httpx.RequestError as e:
            last_error = e
            if attempt < _MAX_POLL_RETRIES - 1:
                await asyncio.sleep(_POLL_RETRY_BACKOFF)
    return None  # all retries exhausted


async def fetch_logs(
    client: DynatraceClient,
    query: str,
    timeframe: str | None = None,
    max_wait_seconds: int = 120,
) -> dict:
    try:
        request_token = await client.execute(query, timeframe=timeframe)
    except httpx.HTTPStatusError as e:
        return {
            "state": "ERROR",
            "status_code": e.response.status_code,
            "error": _extract_error_message(e),
        }
    except httpx.RequestError as e:
        return {"state": "ERROR", "error": str(e)}

    elapsed = 0
    while elapsed < max_wait_seconds:
        try:
            data = await _poll_with_retry(client, request_token)
        except httpx.HTTPStatusError as e:
            return {
                "state": "ERROR",
                "status_code": e.response.status_code,
                "error": _extract_error_message(e),
                "metadata": {"request_token": request_token},
            }

        if data is None:
            return {
                "state": "ERROR",
                "error": "Falha de rede ao consultar Dynatrace após 3 tentativas.",
                "metadata": {"request_token": request_token},
            }

        state = data.get("state")

        if state == "SUCCEEDED":
            result_block = data.get("result", data)
            records = result_block.get("records", [])
            return {
                "state": "SUCCEEDED",
                "records": records,
                "metadata": {
                    "total": result_block.get("totalCount", len(records)),
                    "returned": len(records),
                    "request_token": request_token,
                },
            }

        if state == "FAILED":
            error = data.get("error", {})
            message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            return {
                "state": "FAILED",
                "error": message,
                "metadata": {"request_token": request_token},
            }

        await asyncio.sleep(1)
        elapsed += 1

    return {
        "state": "TIMEOUT",
        "message": (
            f"A query não completou em {max_wait_seconds} segundos. "
            f"Chame poll_query imediatamente com o request_token '{request_token}' para recuperar os resultados."
        ),
        "metadata": {
            "request_token": request_token,
            "progress": data.get("progress") if data else None,
        },
    }
```

- [ ] **Step 4: Run the new tests**

```bash
uv run pytest tests/test_tools.py -v
```

Expected: all PASS. Note: `test_fetch_logs_poll_network_error` now expects 3 retries — update that test's assertion if it checked `call_count`:

```python
# In test_fetch_logs_poll_network_error, update to:
async def test_fetch_logs_poll_network_error(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.side_effect = httpx.ConnectError("connection refused")
    result = await fetch_logs(mock_client, query="fetch logs", max_wait_seconds=1)
    assert result["state"] == "ERROR"
    assert result["metadata"]["request_token"] == "token=="
```

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dynatrace_logs/tools.py tests/test_tools.py
git commit -m "feat: robust polling with retry and 120s default timeout"
```

---

## Task 4: Automatic query enrichment with Log message extraction

**Files:**
- Modify: `src/mcp_dynatrace_logs/tools.py`
- Test: `tests/test_tools.py`

- [ ] **Step 1: Write failing tests for query enrichment**

Add to `tests/test_tools.py`:

```python
from mcp_dynatrace_logs.tools import _enrich_query


def test_enrich_query_injects_block_when_no_fieldsadd():
    query = "fetch logs | filter contains(content, \"error\") | sort timestamp desc | limit 10"
    enriched = _enrich_query(query)
    assert "fieldsAdd" in enriched
    assert "Log message" in enriched
    # sort and limit must remain at the end
    assert enriched.index("sort timestamp desc") > enriched.index("Log message")
    assert enriched.index("limit 10") > enriched.index("Log message")


def test_enrich_query_passthrough_when_fieldsadd_present():
    query = "fetch logs | fieldsAdd myField = content | sort timestamp desc"
    enriched = _enrich_query(query)
    assert enriched == query


def test_enrich_query_no_sort_no_limit():
    query = "fetch logs | filter contains(content, \"test\")"
    enriched = _enrich_query(query)
    assert "fieldsAdd" in enriched
    assert "Log message" in enriched


def test_enrich_query_with_only_limit():
    query = "fetch logs | filter contains(content, \"test\") | limit 5"
    enriched = _enrich_query(query)
    assert "fieldsAdd" in enriched
    # limit must be after the enrichment block
    assert enriched.index("limit 5") > enriched.index("Log message")


async def test_fetch_logs_enriches_query(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.return_value = {
        "state": "SUCCEEDED",
        "records": [{"content": "line", "Log message": "hello"}],
    }
    await fetch_logs(mock_client, query="fetch logs | filter contains(content, \"test\")")
    called_query = mock_client.execute.call_args[0][0]
    assert "fieldsAdd" in called_query
    assert "Log message" in called_query


async def test_fetch_logs_does_not_enrich_if_fieldsadd_present(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.return_value = {"state": "SUCCEEDED", "records": []}
    original = "fetch logs | fieldsAdd x = content | sort timestamp desc"
    await fetch_logs(mock_client, query=original)
    called_query = mock_client.execute.call_args[0][0]
    assert called_query == original
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_tools.py::test_enrich_query_injects_block_when_no_fieldsadd tests/test_tools.py::test_enrich_query_passthrough_when_fieldsadd_present tests/test_tools.py::test_enrich_query_no_sort_no_limit tests/test_tools.py::test_enrich_query_with_only_limit tests/test_tools.py::test_fetch_logs_enriches_query tests/test_tools.py::test_fetch_logs_does_not_enrich_if_fieldsadd_present -v
```

Expected: FAIL (`_enrich_query` does not exist yet).

- [ ] **Step 3: Add _enrich_query and wire it into fetch_logs**

Add the following to `src/mcp_dynatrace_logs/tools.py` (before `fetch_logs`):

```python
_LOG_MESSAGE_ENRICHMENT = """\
| fieldsAdd __attributes_array = array(msg,message,event,description,details)
| fieldsAdd __log_message_attr = arrayFirst(iCollectArray(if(__attributes_array[]!="", __attributes_array[])))
| parse content, "JSON:'__parsed_json'", parsingPrerequisite: isNull(__log_message_attr) and startsWith(content, "{")
| fieldsAdd __json_fields_array = array(__parsed_json[`message`],__parsed_json[`@message`],__parsed_json[`msg`],__parsed_json[`@mt`],__parsed_json[`@m`],__parsed_json[`body`],__parsed_json[`eventName`],__parsed_json[`textPayload`][`message`],__parsed_json[`textPayload`],__parsed_json[`protoPayload`][`@type`],__parsed_json[`protoPayload`][`message`],__parsed_json[`jsonPayload`][`message`],__parsed_json[`messageObject`][`message`],__parsed_json[`properties`][`message`],__parsed_json[`properties`][`statusMessage`],__parsed_json[`properties`][`status`][`additionalDetails`],__parsed_json[`properties`][`log`],__parsed_json[`properties`][`Log`],__parsed_json[`properties`][`Result`],__parsed_json[`content`][`detail`][`event`],__parsed_json[`Body`][`Value`])
| fieldsAdd `Log message` = toString(coalesce(__log_message_attr,arrayFirst(iCollectArray(if(__json_fields_array[]!="", __json_fields_array[])))))
| parse coalesce(`Log message`, content), "(DATA (' '|SPACE))? ('msg'|'message'|'Message') '=' DQS:'__log_message_kv'", parsingPrerequisite: matchesValue(coalesce(`Log message`, content), {"*msg=*","*message=*","*Message=*"}, caseSensitive:true)
| fieldsAdd `Log message` = coalesce(__log_message_kv, `Log message`)
| fieldsRemove __parsed_json, __log_message_attr, __log_message_kv, __attributes_array, __json_fields_array"""


def _enrich_query(query: str) -> str:
    """Inject the Dynatrace Log message enrichment block if not already present.

    The enrichment block is inserted before any trailing | sort or | limit clause
    so the pipeline order matches what Dynatrace expects.
    Queries already containing 'fieldsAdd' are returned unchanged.
    """
    if "fieldsAdd" in query:
        return query

    # Find the position of the last | sort or | limit clause (whichever comes first)
    # so we can insert the enrichment block before it.
    import re
    # Match | sort or | limit at the pipe level (may be preceded by whitespace/newline)
    tail_match = re.search(r"(\|\s*(?:sort|limit)\b.*)", query, re.IGNORECASE | re.DOTALL)
    if tail_match:
        insert_at = tail_match.start()
        return query[:insert_at].rstrip() + "\n" + _LOG_MESSAGE_ENRICHMENT + "\n" + query[insert_at:]
    else:
        return query.rstrip() + "\n" + _LOG_MESSAGE_ENRICHMENT
```

Then update the first line of `fetch_logs` where the query is used:

```python
async def fetch_logs(
    client: DynatraceClient,
    query: str,
    timeframe: str | None = None,
    max_wait_seconds: int = 120,
) -> dict:
    query = _enrich_query(query)   # <-- add this line
    try:
        request_token = await client.execute(query, timeframe=timeframe)
    ...
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dynatrace_logs/tools.py tests/test_tools.py
git commit -m "feat: auto-enrich DQL queries with Dynatrace Log message extraction"
```

---

## Task 5: Final verification

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest -v
```

Expected: all tests PASS, no warnings about asyncio.

- [ ] **Step 2: Verify the MCP server starts cleanly**

```bash
uv run mcp-dynatrace-logs --help 2>&1 || echo "server started (expected no crash)"
```

Expected: no ImportError or syntax error.

- [ ] **Step 3: Final commit if needed**

If any last-minute fixes were made:

```bash
git add -p
git commit -m "fix: final adjustments from verification"
```
