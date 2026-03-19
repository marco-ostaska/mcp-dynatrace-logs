# MCP Dynatrace Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python MCP server that exposes two tools (`fetch_logs`, `poll_query`) for querying Dynatrace logs via DQL.

**Architecture:** A thin async HTTP client wraps the two Dynatrace API endpoints; the tools layer orchestrates the POST+poll loop and normalizes all responses into structured dicts; the MCP server registers both tools and validates credentials at startup.

**Tech Stack:** Python 3.11+, uv, mcp[cli], httpx, python-dotenv, pytest, pytest-asyncio, respx

---

## File Map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Dependencies, package metadata, CLI entrypoint |
| `src/mcp_dynatrace_logs/__init__.py` | Empty package marker |
| `src/mcp_dynatrace_logs/client.py` | Raw HTTP calls to Dynatrace API (execute + poll) |
| `src/mcp_dynatrace_logs/tools.py` | fetch_logs and poll_query logic, response normalization |
| `src/mcp_dynatrace_logs/server.py` | MCP server init, env var validation, tool registration |
| `tests/__init__.py` | Empty |
| `tests/test_client.py` | Tests for HTTP client using respx mocks |
| `tests/test_tools.py` | Tests for tool logic using mocked client |
| `tests/test_server.py` | Tests for startup env var validation |
| `.env.example` | Example credentials file for local dev |
| `README.md` | Setup and usage instructions |

---

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/mcp_dynatrace_logs/__init__.py`
- Create: `tests/__init__.py`
- Create: `.env.example`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "mcp-dynatrace-logs"
version = "0.1.0"
description = "MCP server for querying Dynatrace logs via DQL"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.0.0",
    "httpx>=0.27.0",
    "python-dotenv>=1.0.0",
]

[project.scripts]
mcp-dynatrace-logs = "mcp_dynatrace_logs.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mcp_dynatrace_logs"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "respx>=0.21.0",
]
```

- [ ] **Step 2: Create package and test dirs**

```bash
mkdir -p src/mcp_dynatrace_logs tests
touch src/mcp_dynatrace_logs/__init__.py tests/__init__.py
```

- [ ] **Step 3: Create `.env.example`**

Write this content to `.env.example`:

```
DYNATRACE_URL=https://your-tenant.apps.dynatrace.com
DYNATRACE_API_TOKEN=dt0s16.XXXX.YYYY
```

- [ ] **Step 4: Install dependencies**

```bash
uv sync --dev
```

Expected: lock file created, no errors.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/ tests/ .env.example
git commit -m "chore: scaffold project structure"
```

---

## Task 2: HTTP Client

**Files:**
- Create: `src/mcp_dynatrace_logs/client.py`
- Create: `tests/test_client.py`

The client is responsible for the raw HTTP layer only. It raises `httpx.HTTPStatusError` for 4xx/5xx and `httpx.RequestError` for network failures — the tools layer catches these.

- [ ] **Step 1: Write failing tests for `execute()`**

```python
# tests/test_client.py
import json
import pytest
import httpx
import respx
from mcp_dynatrace_logs.client import DynatraceClient

BASE_URL = "https://test.dynatrace.com"
TOKEN = "test-token"


@pytest.fixture
def client():
    return DynatraceClient(base_url=BASE_URL, token=TOKEN)


@respx.mock
async def test_execute_returns_request_token(client):
    respx.post(f"{BASE_URL}/platform/storage/query/v1/query:execute").mock(
        return_value=httpx.Response(200, json={"requestToken": "abc=="})
    )
    token = await client.execute("fetch logs | limit 10")
    assert token == "abc=="


@respx.mock
async def test_execute_with_timeframe(client):
    route = respx.post(f"{BASE_URL}/platform/storage/query/v1/query:execute").mock(
        return_value=httpx.Response(200, json={"requestToken": "abc=="})
    )
    await client.execute("fetch logs | limit 10", timeframe="3d")
    body = route.calls[0].request.read()
    parsed = json.loads(body)
    assert parsed["defaultTimeframeStart"] == "now()-3d"


@respx.mock
async def test_execute_raises_on_4xx(client):
    respx.post(f"{BASE_URL}/platform/storage/query/v1/query:execute").mock(
        return_value=httpx.Response(400, json={"error": "bad query"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.execute("invalid dql")


@respx.mock
async def test_poll_returns_response(client):
    respx.get(f"{BASE_URL}/platform/storage/query/v1/query:poll").mock(
        return_value=httpx.Response(200, json={"state": "SUCCEEDED", "records": []})
    )
    result = await client.poll("abc==")
    assert result["state"] == "SUCCEEDED"


@respx.mock
async def test_poll_sends_correct_token(client):
    route = respx.get(f"{BASE_URL}/platform/storage/query/v1/query:poll").mock(
        return_value=httpx.Response(200, json={"state": "RUNNING"})
    )
    await client.poll("mytoken==")
    assert "mytoken==" in str(route.calls[0].request.url)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_client.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — client does not exist yet.

- [ ] **Step 3: Implement `client.py`**

```python
# src/mcp_dynatrace_logs/client.py
import httpx


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
            body["defaultTimeframeStart"] = f"now()-{timeframe}"

        async with httpx.AsyncClient() as http:
            response = await http.post(
                f"{self._base_url}/platform/storage/query/v1/query:execute",
                headers=self._headers,
                json=body,
            )
            response.raise_for_status()
            return response.json()["requestToken"]

    async def poll(self, request_token: str) -> dict:
        """GET query:poll. Returns the full response JSON."""
        async with httpx.AsyncClient() as http:
            response = await http.get(
                f"{self._base_url}/platform/storage/query/v1/query:poll",
                headers=self._headers,
                params={"request-token": request_token},
            )
            response.raise_for_status()
            return response.json()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_client.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dynatrace_logs/client.py tests/test_client.py
git commit -m "feat: add Dynatrace HTTP client with execute and poll"
```

---

## Task 3: Tool Implementations

**Files:**
- Create: `src/mcp_dynatrace_logs/tools.py`
- Create: `tests/test_tools.py`

The tools layer catches all exceptions from the client and returns structured dicts. No exception should propagate to the MCP layer.

- [ ] **Step 1: Write failing tests for `fetch_logs`**

```python
# tests/test_tools.py
import pytest
import httpx
from unittest.mock import AsyncMock, patch
from mcp_dynatrace_logs.tools import fetch_logs, poll_query


@pytest.fixture
def mock_client():
    client = AsyncMock()
    return client


async def test_fetch_logs_success(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.return_value = {
        "state": "SUCCEEDED",
        "records": [{"content": "log line"}],
        "progress": 100,
    }
    result = await fetch_logs(mock_client, query="fetch logs | limit 1")
    assert result["state"] == "SUCCEEDED"
    assert result["records"] == [{"content": "log line"}]
    assert result["metadata"]["request_token"] == "token=="
    assert result["metadata"]["returned"] == 1


async def test_fetch_logs_with_timeframe(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.return_value = {"state": "SUCCEEDED", "records": [], "progress": 100}
    await fetch_logs(mock_client, query="fetch logs", timeframe="3d")
    mock_client.execute.assert_called_once_with("fetch logs", timeframe="3d")


async def test_fetch_logs_timeout(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.return_value = {"state": "RUNNING", "progress": 10}
    result = await fetch_logs(mock_client, query="fetch logs", max_wait_seconds=0)
    assert result["state"] == "TIMEOUT"
    assert result["metadata"]["request_token"] == "token=="
    assert "request_token" in result["message"]


async def test_fetch_logs_poll_failed(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.return_value = {"state": "FAILED", "error": {"message": "bad query"}}
    result = await fetch_logs(mock_client, query="fetch logs")
    assert result["state"] == "FAILED"
    assert result["metadata"]["request_token"] == "token=="


async def test_fetch_logs_http_error(mock_client):
    mock_client.execute.side_effect = httpx.HTTPStatusError(
        "400", request=AsyncMock(), response=AsyncMock(status_code=400, text="bad request")
    )
    result = await fetch_logs(mock_client, query="bad dql")
    assert result["state"] == "ERROR"
    assert result["status_code"] == 400


async def test_fetch_logs_network_error(mock_client):
    mock_client.execute.side_effect = httpx.ConnectError("connection refused")
    result = await fetch_logs(mock_client, query="fetch logs")
    assert result["state"] == "ERROR"
    assert "connection refused" in result["error"]


async def test_poll_query_running(mock_client):
    mock_client.poll.return_value = {"state": "RUNNING", "progress": 50}
    result = await poll_query(mock_client, request_token="token==")
    assert result["state"] == "RUNNING"
    assert result["progress"] == 50
    assert result["metadata"]["request_token"] == "token=="


async def test_poll_query_succeeded(mock_client):
    mock_client.poll.return_value = {
        "state": "SUCCEEDED",
        "records": [{"content": "line"}],
        "progress": 100,
    }
    result = await poll_query(mock_client, request_token="token==")
    assert result["state"] == "SUCCEEDED"
    assert result["records"] == [{"content": "line"}]
    assert result["progress"] == 100


async def test_poll_query_network_error(mock_client):
    mock_client.poll.side_effect = httpx.ConnectError("connection refused")
    result = await poll_query(mock_client, request_token="token==")
    assert result["state"] == "ERROR"
    assert "connection refused" in result["error"]
    assert result["metadata"]["request_token"] == "token=="


async def test_fetch_logs_poll_network_error(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.side_effect = httpx.ConnectError("connection refused")
    result = await fetch_logs(mock_client, query="fetch logs", max_wait_seconds=1)
    assert result["state"] == "ERROR"
    assert "connection refused" in result["error"]
    assert result["metadata"]["request_token"] == "token=="


async def test_poll_query_failed(mock_client):
    mock_client.poll.return_value = {"state": "FAILED", "error": {"message": "timeout"}}
    result = await poll_query(mock_client, request_token="token==")
    assert result["state"] == "FAILED"
    assert "timeout" in result["error"]


async def test_poll_query_http_error(mock_client):
    mock_client.poll.side_effect = httpx.HTTPStatusError(
        "401", request=AsyncMock(), response=AsyncMock(status_code=401, text="unauthorized")
    )
    result = await poll_query(mock_client, request_token="token==")
    assert result["state"] == "ERROR"
    assert result["status_code"] == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_tools.py -v
```

Expected: `ImportError` — tools module does not exist yet.

- [ ] **Step 3: Implement `tools.py`**

```python
# src/mcp_dynatrace_logs/tools.py
import asyncio
import httpx
from mcp_dynatrace_logs.client import DynatraceClient


def _extract_error_message(e: httpx.HTTPStatusError) -> str:
    try:
        return e.response.json().get("error", {}).get("message", e.response.text)
    except Exception:
        return e.response.text


async def fetch_logs(
    client: DynatraceClient,
    query: str,
    timeframe: str | None = None,
    max_wait_seconds: int = 30,
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
            data = await client.poll(request_token)
        except httpx.HTTPStatusError as e:
            return {
                "state": "ERROR",
                "status_code": e.response.status_code,
                "error": _extract_error_message(e),
                "metadata": {"request_token": request_token},
            }
        except httpx.RequestError as e:
            return {
                "state": "ERROR",
                "error": str(e),
                "metadata": {"request_token": request_token},
            }

        state = data.get("state")

        if state == "SUCCEEDED":
            records = data.get("records", [])
            return {
                "state": "SUCCEEDED",
                "records": records,
                "metadata": {
                    "total": data.get("totalCount", len(records)),
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
            f"Query did not complete within {max_wait_seconds} seconds. "
            f"Use poll_query with the request_token to retrieve results."
        ),
        "metadata": {"request_token": request_token},
    }


async def poll_query(client: DynatraceClient, request_token: str) -> dict:
    try:
        data = await client.poll(request_token)
    except httpx.HTTPStatusError as e:
        return {
            "state": "ERROR",
            "status_code": e.response.status_code,
            "error": _extract_error_message(e),
            "metadata": {"request_token": request_token},
        }
    except httpx.RequestError as e:
        return {
            "state": "ERROR",
            "error": str(e),
            "metadata": {"request_token": request_token},
        }

    state = data.get("state")
    result: dict = {
        "state": state,
        "metadata": {"request_token": request_token},
    }

    if "progress" in data:
        result["progress"] = data["progress"]

    if state == "SUCCEEDED":
        result["records"] = data.get("records", [])
    elif state == "FAILED":
        error = data.get("error", {})
        result["error"] = error.get("message", str(error)) if isinstance(error, dict) else str(error)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_tools.py -v
```

Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mcp_dynatrace_logs/tools.py tests/test_tools.py
git commit -m "feat: implement fetch_logs and poll_query tools"
```

---

## Task 4: MCP Server

**Files:**
- Create: `src/mcp_dynatrace_logs/server.py`
- Create: `tests/test_server.py`

The server validates env vars at startup via `_build_client()`, uses lazy global initialization to avoid side effects on import, and registers both tools with the MCP SDK. Tests call `_build_client()` directly — no module reload tricks needed.

- [ ] **Step 1: Write failing tests for env var validation**

```python
# tests/test_server.py
import os
import pytest
from unittest.mock import patch


def test_missing_dynatrace_url_raises():
    with patch.dict(os.environ, {"DYNATRACE_API_TOKEN": "token"}, clear=True):
        with pytest.raises(EnvironmentError, match="DYNATRACE_URL"):
            from mcp_dynatrace_logs.server import _build_client
            _build_client()


def test_missing_dynatrace_token_raises():
    with patch.dict(os.environ, {"DYNATRACE_URL": "https://test.dynatrace.com"}, clear=True):
        with pytest.raises(EnvironmentError, match="DYNATRACE_API_TOKEN"):
            from mcp_dynatrace_logs.server import _build_client
            _build_client()


def test_both_env_vars_present_returns_client():
    env = {
        "DYNATRACE_URL": "https://test.dynatrace.com",
        "DYNATRACE_API_TOKEN": "mytoken",
    }
    with patch.dict(os.environ, env, clear=True):
        from mcp_dynatrace_logs.server import _build_client
        client = _build_client()
        assert client is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_server.py -v
```

Expected: `ImportError` — server module does not exist yet.

- [ ] **Step 3: Implement `server.py`**

```python
# src/mcp_dynatrace_logs/server.py
import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp_dynatrace_logs.client import DynatraceClient
from mcp_dynatrace_logs import tools

load_dotenv()

mcp = FastMCP("dynatrace-logs")

_client: DynatraceClient | None = None


def _build_client() -> DynatraceClient:
    """Build a DynatraceClient from environment variables. Raises EnvironmentError if vars are missing."""
    url = os.environ.get("DYNATRACE_URL")
    token = os.environ.get("DYNATRACE_API_TOKEN")
    if not url:
        raise EnvironmentError("DYNATRACE_URL environment variable is not set.")
    if not token:
        raise EnvironmentError("DYNATRACE_API_TOKEN environment variable is not set.")
    return DynatraceClient(base_url=url, token=token)


def _get_client() -> DynatraceClient:
    """Return the module-level client, initializing it on first call."""
    global _client
    if _client is None:
        _client = _build_client()
    return _client


@mcp.tool()
async def fetch_logs(
    query: str,
    timeframe: str | None = None,
    max_wait_seconds: int = 30,
) -> dict:
    """
    Execute a DQL query against Dynatrace logs and return results.

    Args:
        query: Full DQL string, e.g. "fetch logs | filter context=\"*xxx*\" | limit 100"
        timeframe: Optional time offset like "3d" or "1h". Added as defaultTimeframeStart.
        max_wait_seconds: How long to poll before returning a TIMEOUT state (default 30).

    Returns a dict with "state" key: SUCCEEDED, FAILED, TIMEOUT, or ERROR.
    On TIMEOUT, use poll_query with the returned request_token to retrieve results later.
    """
    return await tools.fetch_logs(_get_client(), query=query, timeframe=timeframe, max_wait_seconds=max_wait_seconds)


@mcp.tool()
async def poll_query(request_token: str) -> dict:
    """
    Poll a Dynatrace query by request token. Use after fetch_logs returns TIMEOUT.

    Args:
        request_token: The token returned by a previous fetch_logs or poll_query call.

    Returns a dict with "state" key: RUNNING, SUCCEEDED, FAILED, or ERROR.
    """
    return await tools.poll_query(_get_client(), request_token=request_token)


def main():
    # Fail fast at startup if credentials are missing
    _get_client()
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_server.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest -v
```

Expected: all tests PASS (no failures, no errors).

- [ ] **Step 6: Commit**

```bash
git add src/mcp_dynatrace_logs/server.py tests/test_server.py
git commit -m "feat: add MCP server with tool registration and env var validation"
```

---

## Task 5: README and Claude Desktop Config

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create `README.md`**

```markdown
# mcp-dynatrace-logs

MCP server for querying Dynatrace logs via DQL.

## Setup

### 1. Install

```bash
uv sync
```

### 2. Configure credentials

Copy `.env.example` to `.env` and fill in your values:

```bash
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

## Tools

### `fetch_logs`

Executes a DQL query and polls until results are ready.

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | str | yes | — | Full DQL string |
| `timeframe` | str | no | — | Time offset: `3d`, `1h`, `30m` |
| `max_wait_seconds` | int | no | 30 | Polling timeout |

### `poll_query`

Manually poll a query using a `request_token` from a previous call.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `request_token` | str | yes | Token from fetch_logs or poll_query |

## Running tests

```bash
uv run pytest -v
```
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup and Claude Desktop config"
```
