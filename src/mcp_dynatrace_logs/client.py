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
