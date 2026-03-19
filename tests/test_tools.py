import pytest
import httpx
from unittest.mock import AsyncMock
from mcp_dynatrace_logs.tools import fetch_logs, poll_query


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


async def test_fetch_logs_poll_network_error(mock_client):
    mock_client.execute.return_value = "token=="
    mock_client.poll.side_effect = httpx.ConnectError("connection refused")
    result = await fetch_logs(mock_client, query="fetch logs", max_wait_seconds=1)
    assert result["state"] == "ERROR"
    assert "connection refused" in result["error"]
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
    result = await poll_query(mock_client, request_token="token==")
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
