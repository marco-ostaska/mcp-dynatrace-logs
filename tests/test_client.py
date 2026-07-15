import json
import re
from urllib.parse import unquote
import pytest
import httpx
import respx
import asyncio
from mcp_dynatrace_logs.client import (
    DynatraceClient,
    DynatraceSSRFError,
    InvalidDynatraceUrlError,
    _timeframe_to_iso,
    _validate_dynatrace_url,
    _sanitize_request_for_error,
    _sanitize_response_for_error,
    _resolve_and_validate_host,
    _ValidatedAsyncHTTPTransport,
)

BASE_URL = "https://test.dynatrace.com"
TOKEN = "test-token"


@pytest.fixture
async def client():
    # Use a plain httpx client so respx can mock without real DNS lookups.
    http = httpx.AsyncClient()
    c = DynatraceClient(base_url=BASE_URL, token=TOKEN, http_client=http)
    try:
        yield c
    finally:
        await http.aclose()




@pytest.mark.parametrize(
    "url",
    [
        "http://test.dynatrace.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.1",
        "https://169.254.169.254",
        "https://metadata.google.internal",
        "https://attacker.com",
    ],
)
def test_validate_dynatrace_url_rejects_unsafe(url):
    with pytest.raises(InvalidDynatraceUrlError):
        _validate_dynatrace_url(url)


def test_exception_does_not_leak_authorization_header():
    request = httpx.Request("GET", "https://test.dynatrace.com", headers={"Authorization": "Bearer secret"})
    sanitized = _sanitize_request_for_error(request)
    assert "authorization" not in sanitized.headers
    assert "Bearer secret" not in str(sanitized.headers)


def test_sanitized_response_strips_authorization_from_response_request():
    request = httpx.Request("GET", "https://test.dynatrace.com", headers={"Authorization": "Bearer secret"})
    response = httpx.Response(401, text="unauthorized", request=request)
    sanitized = _sanitize_response_for_error(response)
    assert "authorization" not in sanitized.request.headers
    assert "Bearer secret" not in str(sanitized.request.headers)


def test_sanitized_response_preserves_gzip_content():
    import gzip
    request = httpx.Request("GET", "https://test.dynatrace.com", headers={"Authorization": "Bearer secret"})
    original_payload = b'{"error":"unauthorized"}'
    gzipped = gzip.compress(original_payload)
    response = httpx.Response(
        401,
        content=gzipped,
        headers={"content-encoding": "gzip", "content-type": "application/json"},
        request=request,
    )
    sanitized = _sanitize_response_for_error(response)
    assert sanitized.content == original_payload
    assert "authorization" not in sanitized.request.headers


@pytest.mark.parametrize(
    "hostname",
    [
        "localhost",
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "metadata.google.internal",
    ],
)
async def test_resolve_and_validate_host_rejects_private(hostname):
    with pytest.raises(DynatraceSSRFError):
        await _resolve_and_validate_host(hostname)


async def test_resolve_and_validate_host_accepts_public():
    # github.com is a stable public hostname for this smoke test.
    await _resolve_and_validate_host("github.com")


async def test_validated_transport_blocks_private_host():
    """The custom transport must reject a request whose host resolves to a private IP."""
    transport = _ValidatedAsyncHTTPTransport()
    request = httpx.Request("GET", "https://localhost/")
    with pytest.raises((DynatraceSSRFError, httpx.ConnectError)):
        # Localhost is rejected either by IP validation or by connect failure.
        await transport.handle_async_request(request)
    await transport.aclose()


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
