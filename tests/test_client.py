import json
from urllib.parse import unquote
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
    assert "mytoken==" in unquote(str(route.calls[0].request.url))
