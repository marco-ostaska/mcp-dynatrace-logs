import asyncio
import ipaddress
import re
import socket
import httpx
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse


class InvalidDynatraceUrlError(ValueError):
    """Raised when the configured DYNATRACE_URL is not a valid Dynatrace endpoint."""


class DynatraceSSRFError(InvalidDynatraceUrlError):
    """Raised when a Dynatrace-looking hostname resolves to a non-public IP."""



def _is_private_or_reserved_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True for any non-publicly-routable IP address."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_link_local
        or ip.is_unspecified
    )


def _is_private_or_reserved_host(hostname: str) -> bool:
    """Return True if hostname is a private/reserved IP or metadata endpoint."""
    if hostname in ("localhost", "metadata.google.internal"):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        return _is_private_or_reserved_ip(ip)
    except ValueError:
        return False


def _validate_dynatrace_url(url: str) -> str:
    """Validate and normalize a Dynatrace base URL.

    Enforces HTTPS, a public IP/hostname, and optionally a Dynatrace-looking
    domain. Raises InvalidDynatraceUrlError on any unsafe input.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise InvalidDynatraceUrlError("DYNATRACE_URL must use HTTPS scheme.")
    hostname = parsed.hostname
    if not hostname:
        raise InvalidDynatraceUrlError("DYNATRACE_URL is missing a hostname.")
    if _is_private_or_reserved_host(hostname):
        raise InvalidDynatraceUrlError(
            "DYNATRACE_URL points to a private or internal endpoint and is not allowed."
        )
    # Dynatrace SaaS and Managed ActiveGate hostnames should contain dynatrace.com,
    # dynatrace-managed.com, or a known ActiveGate suffix. This prevents SSRF to
    # arbitrary public hosts while remaining configurable for valid deployments.
    if not any(
        hostname.lower().endswith(suffix)
        for suffix in ("dynatrace.com", "dynatrace-managed.com", "apps.dynatrace.com")
    ):
        raise InvalidDynatraceUrlError(
            "DYNATRACE_URL must be a Dynatrace endpoint (*.dynatrace.com, *.apps.dynatrace.com, or *.dynatrace-managed.com)."
        )
    return url.rstrip("/")


def _sanitize_request_for_error(request: httpx.Request) -> httpx.Request:
    """Return a copy of the request with the Authorization header removed.

    Prevents httpx.HTTPStatusError from carrying the bearer token in the
    exception object that may be logged or returned to the MCP client.
    """
    headers = httpx.Headers(request.headers)
    headers.pop("authorization", None)
    return httpx.Request(
        method=request.method,
        url=request.url,
        headers=headers,
        content=request.content,
    )


async def _resolve_and_validate_host(hostname: str) -> None:
    """Resolve hostname and reject any non-public IP to block DNS-rebinding SSRF."""
    try:
        loop = asyncio.get_running_loop()
        # getaddrinfo handles both IPv4 and IPv6.
        infos = await loop.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise DynatraceSSRFError(
            f"Could not resolve DYNATRACE_URL hostname {hostname!r}."
        ) from e
    for family, _, _, _, sockaddr in infos:
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_private_or_reserved_ip(ip):
            raise DynatraceSSRFError(
                f"DYNATRACE_URL hostname {hostname!r} resolved to non-public IP {ip}."
            )


class _ValidatedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport that re-validates the request host before each request.

    Every httpx request flows through handle_async_request, so validating the
    target hostname here closes the DNS-rebinding SSRF window left by only
    checking the configured URL string at startup.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await _resolve_and_validate_host(request.url.host)
        return await super().handle_async_request(request)


def _sanitize_response_for_error(response: httpx.Response) -> httpx.Response:
    """Return a copy of the response whose request has the Authorization header removed.

    httpx.HTTPStatusError carries both `exc.request` and `exc.response.request`.
    Sanitizing only the request attached to the exception leaves the original
    token accessible through `exc.response.request`; this removes it from both.
    """
    sanitized_request = _sanitize_request_for_error(response.request)
    return httpx.Response(
        status_code=response.status_code,
        headers=response.headers,
        content=response.content,
        request=sanitized_request,
    )


def _timeframe_to_iso(timeframe: str) -> str:
    """Convert a relative timeframe like '1h', '3d', '30m' to an ISO 8601 UTC timestamp.

    The Dynatrace query:execute API expects defaultTimeframeStart as an ISO 8601
    timestamp, not a DQL expression like 'now()-1h'.
    """
    match = re.fullmatch(r"(\d+)([smhd])", timeframe)
    if not match:
        raise ValueError(
            f"Invalid timeframe {timeframe!r}. Expected <number><unit>, e.g. '1h', '3d', '30m'."
        )
    value, unit = int(match.group(1)), match.group(2)
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    delta = timedelta(**{units[unit]: value})
    return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raise_for_status(response: httpx.Response) -> None:
    """Raise HTTPStatusError with an actionable message based on status code.

    The Authorization header is stripped from both the attached request and
    the response.request to avoid leaking the API token in exception traces.
    """
    sanitized_response = _sanitize_response_for_error(response)
    request = sanitized_response.request
    if response.status_code == 401:
        raise httpx.HTTPStatusError(
            "Invalid or expired API token. Check DYNATRACE_API_TOKEN.",
            request=request,
            response=sanitized_response,
        )
    if response.status_code == 403:
        raise httpx.HTTPStatusError(
            "Token lacks log read permission. Check the token scopes in Dynatrace.",
            request=request,
            response=sanitized_response,
        )
    if response.status_code == 400:
        try:
            error = response.json().get("error", {})
            api_msg = error.get("message", response.text) if isinstance(error, dict) else response.text
        except Exception:
            api_msg = response.text
        raise httpx.HTTPStatusError(
            f"{api_msg} — check DQL syntax.",
            request=request,
            response=sanitized_response,
        )
    response.raise_for_status()


def _wrap_request_error(base_url: str, e: httpx.RequestError) -> httpx.RequestError:
    """Return a more actionable RequestError subclass for known network failures."""
    if isinstance(e, httpx.ConnectError):
        return httpx.ConnectError(
            f"Could not connect to Dynatrace at {base_url}. "
            f"Check DYNATRACE_URL and network connectivity. Detail: {e}"
        )
    if isinstance(e, httpx.TimeoutException):
        return httpx.TimeoutException(
            f"Timeout connecting to Dynatrace at {base_url}. "
            f"Check DYNATRACE_URL and network connectivity."
        )
    return e


class DynatraceClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        http_client: httpx.AsyncClient | None = None,
    ):
        self._base_url = _validate_dynatrace_url(base_url)
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        # Reuse a shared client to benefit from connection pooling, consistent
        # timeouts/limits, and a stable SSL context across polling calls.
        # The custom transport validates resolved IPs to prevent DNS-rebinding SSRF.
        # When an http_client is injected (e.g. tests), it is used as-is so mock
        # transports like respx can operate without real DNS resolution.
        self._http = http_client or httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(connect=10, read=120, write=60, pool=30),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            transport=_ValidatedAsyncHTTPTransport(),
        )
        self._owns_http_client = http_client is None

    async def close(self) -> None:
        """Close the underlying HTTP client if we created it."""
        if self._owns_http_client:
            await self._http.aclose()

    async def execute(self, query: str, timeframe: str | None = None) -> str:
        """POST query:execute. Returns the request token."""
        body: dict = {"query": query}
        if timeframe:
            body["defaultTimeframeStart"] = _timeframe_to_iso(timeframe)
            body["defaultTimeframeEnd"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            response = await self._http.post(
                f"{self._base_url}/platform/storage/query/v1/query:execute",
                json=body,
            )
            _raise_for_status(response)
            return response.json()["requestToken"]
        except httpx.RequestError as e:
            raise _wrap_request_error(self._base_url, e) from e

    async def poll(self, request_token: str) -> dict:
        """GET query:poll. Returns the full response JSON."""
        try:
            response = await self._http.get(
                f"{self._base_url}/platform/storage/query/v1/query:poll",
                params={"request-token": request_token},
            )
            _raise_for_status(response)
            return response.json()
        except httpx.RequestError as e:
            raise _wrap_request_error(self._base_url, e) from e
