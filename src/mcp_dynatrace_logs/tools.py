import asyncio
import httpx
from mcp_dynatrace_logs.client import DynatraceClient

_MAX_POLL_RETRIES = 3
_POLL_RETRY_BACKOFF = 2  # seconds


def _extract_error_message(e: httpx.HTTPStatusError) -> str:
    # Prefer the actionable message baked in by _raise_for_status
    if e.args and e.args[0] and not e.args[0].startswith("Client error"):
        return e.args[0]
    try:
        return e.response.json().get("error", {}).get("message", e.response.text)
    except Exception:
        return e.response.text


async def _poll_with_retry(client: DynatraceClient, request_token: str) -> dict | None:
    """Poll once, retrying up to _MAX_POLL_RETRIES times on transient network errors.

    Returns the poll response dict, or None if all retries are exhausted.
    Raises httpx.HTTPStatusError immediately (no retry) on 4xx/5xx responses.
    """
    for attempt in range(_MAX_POLL_RETRIES):
        try:
            return await client.poll(request_token)
        except httpx.HTTPStatusError:
            raise  # permanent error, don't retry
        except httpx.RequestError:
            if attempt < _MAX_POLL_RETRIES - 1:
                await asyncio.sleep(_POLL_RETRY_BACKOFF)
    return None  # all retries exhausted


async def fetch_logs(
    client: DynatraceClient,
    query: str,
    timeframe: str | None = None,
    max_wait_seconds: int = 120,
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

    data: dict | None = None
    elapsed = 0
    while elapsed < max_wait_seconds:
        try:
            data = await _poll_with_retry(client, request_token)
        except httpx.HTTPStatusError as e:
            return {
                "state": "ERROR",
                "status_code": e.response.status_code,
                "error": _extract_error_message(e),
                "metadata": {"request_token": request_token},
            }

        if data is None:
            return {
                "state": "ERROR",
                "error": "Falha de rede ao consultar Dynatrace após 3 tentativas.",
                "metadata": {"request_token": request_token},
            }

        state = data.get("state")

        if state == "SUCCEEDED":
            result_block = data.get("result", data)
            records = result_block.get("records", [])
            return {
                "state": "SUCCEEDED",
                "records": records,
                "metadata": {
                    "total": result_block.get("totalCount", len(records)),
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
            f"A query não completou em {max_wait_seconds} segundos. "
            f"Chame poll_query imediatamente com o request_token '{request_token}' para recuperar os resultados."
        ),
        "metadata": {
            "request_token": request_token,
            "progress": data.get("progress") if data else None,
        },
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
        result_block = data.get("result", data)
        result["records"] = result_block.get("records", [])
    elif state == "FAILED":
        error = data.get("error", {})
        result["error"] = error.get("message", str(error)) if isinstance(error, dict) else str(error)

    return result
