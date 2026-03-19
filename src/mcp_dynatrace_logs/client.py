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
