import asyncio
import httpx
from mcp_dynatrace_logs.client import DynatraceClient


def _extract_error_message(e: httpx.HTTPStatusError) -> str:
    try:
        return e.response.json().get("error", {}).get("message", e.response.text)
    except Exception:
        return e.response.text


async def fetch_logs(
    client: DynatraceClient,
    query: str,
    timeframe: str | None = None,
    max_wait_seconds: int = 30,
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

    elapsed = 0
    while elapsed < max_wait_seconds:
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
            f"Query did not complete within {max_wait_seconds} seconds. "
            f"Use poll_query with the request_token to retrieve results."
        ),
        "metadata": {"request_token": request_token},
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
