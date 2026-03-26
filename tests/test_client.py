import json
import re
from urllib.parse import unquote
import pytest
import httpx
import respx
from mcp_dynatrace_logs.client import DynatraceClient, _timeframe_to_iso

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
    # Must be an ISO 8601 timestamp, not a DQL expression like "now()-3d"
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", parsed["defaultTimeframeStart"])
    # defaultTimeframeEnd must be sent so Dynatrace doesn't cap the window at its 2h default
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", parsed["defaultTimeframeEnd"])
    # end must be after start
    assert parsed["defaultTimeframeEnd"] > parsed["defaultTimeframeStart"]


def test_timeframe_to_iso_format():
    iso = _timeframe_to_iso("24h")
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", iso)


def test_timeframe_to_iso_invalid():
    with pytest.raises(ValueError, match="Invalid timeframe"):
        _timeframe_to_iso("bad")


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
    assert "mytoken==" in unquote(str(route.calls[0].request.url))


@respx.mock
async def test_execute_401_raises_with_message(client):
    respx.post(f"{BASE_URL}/platform/storage/query/v1/query:execute").mock(
        return_value=httpx.Response(401, json={"error": {"message": "unauthorized"}})
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.execute("fetch logs")
    assert "token" in str(exc_info.value).lower()
    assert exc_info.value.response.status_code == 401


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


@respx.mock
async def test_execute_400_includes_api_message(client):
    respx.post(f"{BASE_URL}/platform/storage/query/v1/query:execute").mock(
        return_value=httpx.Response(400, json={"error": {"message": "DQL syntax error at line 1"}})
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.execute("bad dql query")
    assert "DQL syntax error at line 1" in str(exc_info.value)
    assert "DQL syntax" in str(exc_info.value)
