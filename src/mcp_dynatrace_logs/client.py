import re
import httpx
from datetime import datetime, timezone, timedelta


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
                _raise_for_status(response)
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
                _raise_for_status(response)
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
