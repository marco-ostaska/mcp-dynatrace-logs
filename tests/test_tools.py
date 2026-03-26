import pytest
import httpx
from unittest.mock import AsyncMock, patch
from mcp_dynatrace_logs.tools import fetch_logs, poll_query, _enrich_query


@pytest.fixture
def mock_client():
    return AsyncMock()


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
    call_args = mock_client.execute.call_args
    called_query = call_args[0][0]
    assert "fetch logs" in called_query
    assert "fieldsAdd" in called_query
    assert call_args[1]["timeframe"] == "3d"


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


async def test_fetch_logs_poll_network_error(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.side_effect = httpx.ConnectError("connection refused")
    with patch("mcp_dynatrace_logs.tools.asyncio.sleep"):
        result = await fetch_logs(mock_client, query="fetch logs", max_wait_seconds=1)
    assert result["state"] == "ERROR"
    assert result["metadata"]["request_token"] == "token=="


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
    with patch("mcp_dynatrace_logs.tools.asyncio.sleep"):
        result = await poll_query(mock_client, request_token="token==")
    assert result["state"] == "ERROR"
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
    with patch("mcp_dynatrace_logs.tools.asyncio.sleep"):
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
    with patch("mcp_dynatrace_logs.tools.asyncio.sleep"):
        result = await fetch_logs(mock_client, query="fetch logs", max_wait_seconds=10)
    assert result["state"] == "SUCCEEDED"
    assert result["records"] == [{"content": "ok"}]


def test_enrich_query_injects_block_when_no_fieldsadd():
    query = 'fetch logs | filter contains(content, "error") | sort timestamp desc | limit 10'
    enriched = _enrich_query(query)
    assert "fieldsAdd" in enriched
    assert "Log message" in enriched
    # sort and limit must remain at the end
    assert enriched.index("sort timestamp desc") > enriched.index("Log message")
    assert enriched.index("limit 10") > enriched.index("Log message")


def test_enrich_query_passthrough_when_log_message_present():
    query = "fetch logs | fieldsAdd `Log message` = content | sort timestamp desc"
    enriched = _enrich_query(query)
    assert enriched == query


def test_enrich_query_enriches_when_unrelated_fieldsadd_present():
    """A query with fieldsAdd unrelated to Log message should still be enriched."""
    query = "fetch logs | fieldsAdd severity = loglevel | sort timestamp desc"
    enriched = _enrich_query(query)
    assert "`Log message`" in enriched


def test_enrich_query_no_sort_no_limit():
    query = 'fetch logs | filter contains(content, "test")'
    enriched = _enrich_query(query)
    assert "fieldsAdd" in enriched
    assert "Log message" in enriched


def test_enrich_query_skips_when_summarize_present():
    query = 'fetch logs | filter contains(content, "error") | summarize total = count()'
    enriched = _enrich_query(query)
    assert enriched == query


def test_enrich_query_skips_when_makeTimeseries_present():
    query = 'fetch logs | makeTimeseries count(), by: {loglevel}'
    enriched = _enrich_query(query)
    assert enriched == query


def test_enrich_query_with_only_limit():
    query = 'fetch logs | filter contains(content, "test") | limit 5'
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
    await fetch_logs(mock_client, query='fetch logs | filter contains(content, "test")')
    called_query = mock_client.execute.call_args[0][0]
    assert "fieldsAdd" in called_query
    assert "Log message" in called_query


async def test_fetch_logs_does_not_enrich_if_log_message_present(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.return_value = {"state": "SUCCEEDED", "records": []}
    original = "fetch logs | fieldsAdd `Log message` = content | sort timestamp desc"
    await fetch_logs(mock_client, query=original)
    called_query = mock_client.execute.call_args[0][0]
    assert called_query == original
