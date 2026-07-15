import asyncio
import re
import time
import httpx
from mcp_dynatrace_logs.client import DynatraceClient

_LOG_MESSAGE_ENRICHMENT = (
    '| fieldsAdd __attributes_array = array(msg,message,event,description,details)\n'
    '| fieldsAdd __log_message_attr = arrayFirst(iCollectArray(if(__attributes_array[]!="", __attributes_array[])))\n'
    '| parse content, "JSON:\'__parsed_json\'", parsingPrerequisite: isNull(__log_message_attr) and startsWith(content, "{")\n'
    '| fieldsAdd __json_fields_array = array(__parsed_json[`message`],__parsed_json[`@message`],__parsed_json[`msg`],__parsed_json[`@mt`],__parsed_json[`@m`],__parsed_json[`body`],__parsed_json[`eventName`],__parsed_json[`textPayload`][`message`],__parsed_json[`textPayload`],__parsed_json[`protoPayload`][`@type`],__parsed_json[`protoPayload`][`message`],__parsed_json[`jsonPayload`][`message`],__parsed_json[`messageObject`][`message`],__parsed_json[`properties`][`message`],__parsed_json[`properties`][`statusMessage`],__parsed_json[`properties`][`status`][`additionalDetails`],__parsed_json[`properties`][`log`],__parsed_json[`properties`][`Log`],__parsed_json[`properties`][`Result`],__parsed_json[`content`][`detail`][`event`],__parsed_json[`Body`][`Value`])\n'
    '| fieldsAdd `Log message` = toString(coalesce(__log_message_attr,arrayFirst(iCollectArray(if(__json_fields_array[]!="", __json_fields_array[])))))\n'
    '| parse coalesce(`Log message`, content), "(DATA (\' \'|SPACE))? (\'msg\'|\'message\'|\'Message\') \'=\' DQS:\'__log_message_kv\'", parsingPrerequisite: matchesValue(coalesce(`Log message`, content), {"*msg=*","*message=*","*Message=*"}, caseSensitive:true)\n'
    '| fieldsAdd `Log message` = coalesce(__log_message_kv, `Log message`)\n'
    '| fieldsRemove __parsed_json, __log_message_attr, __log_message_kv, __attributes_array, __json_fields_array'
)


_MIN_RESULT_LIMIT = 2000
_LIMIT_PATTERN = re.compile(r"(\|\s*limit\s+)(\d+)", re.IGNORECASE)
_AGGREGATION_COMMANDS = re.compile(
    r"\|\s*(?:summarize|makeTimeseries|makeSmartTimeseries|fieldsRemove|fieldsKeep|lookup|join)\b",
    re.IGNORECASE,
)


def _ensure_min_limit(query: str, min_limit: int = _MIN_RESULT_LIMIT) -> str:
    """Raise any explicit | limit N below min_limit up to min_limit.

    Dynatrace queries with small limits (e.g. 100) frequently return empty
    results for log workloads; this guarantees a useful minimum page size.
    """
    def _raise_limit(match: re.Match) -> str:
        current = int(match.group(2))
        if current < min_limit:
            return match.group(1) + str(min_limit)
        return match.group(0)

    return _LIMIT_PATTERN.sub(_raise_limit, query)


def _enrich_query(query: str) -> str:
    """Inject the Dynatrace Log message enrichment block if not already present.

    Inserted before any trailing | sort or | limit clause.
    Skipped if the query already has `Log message` or uses aggregation commands
    (summarize, makeTimeseries, etc.) that are incompatible with per-record enrichment.

    Also ensures the query never uses a result limit smaller than _MIN_RESULT_LIMIT.
    """
    query = _ensure_min_limit(query)
    if "`Log message`" in query:
        return query
    if _AGGREGATION_COMMANDS.search(query):
        return query
    tail_match = re.search(r"(\|\s*(?:sort|limit)\b.*)", query, re.IGNORECASE | re.DOTALL)
    if tail_match:
        insert_at = tail_match.start()
        return query[:insert_at].rstrip() + "\n" + _LOG_MESSAGE_ENRICHMENT + "\n" + query[insert_at:]
    return query.rstrip() + "\n" + _LOG_MESSAGE_ENRICHMENT

_MAX_POLL_RETRIES = 3
_POLL_RETRY_BACKOFF = 2  # seconds
_MAX_WAIT_SECONDS = 600


def _extract_error_message(e: httpx.HTTPStatusError) -> str:
    # Prefer the actionable message baked in by _raise_for_status
    if e.args and e.args[0] and not e.args[0].startswith("Client error"):
        return e.args[0]
    try:
        return e.response.json().get("error", {}).get("message", e.response.text)
    except Exception:
        return e.response.text


async def _poll_with_retry(client: DynatraceClient, request_token: str) -> dict:
    """Poll once, retrying up to _MAX_POLL_RETRIES times on transient network errors.

    Returns the poll response dict.
    Raises httpx.HTTPStatusError immediately (no retry) on 4xx/5xx responses.
    Raises the last httpx.RequestError if all retries are exhausted.
    """
    last_error: httpx.RequestError | None = None
    for attempt in range(_MAX_POLL_RETRIES):
        try:
            return await client.poll(request_token)
        except httpx.HTTPStatusError:
            raise  # permanent error, don't retry
        except httpx.RequestError as e:
            last_error = e
            if attempt < _MAX_POLL_RETRIES - 1:
                await asyncio.sleep(_POLL_RETRY_BACKOFF)
    if last_error is not None:
        raise last_error
    # Defensive fallback; should be unreachable because last_error is set on the first iteration.
    raise httpx.RequestError("Network failure querying Dynatrace after retries.")


async def fetch_logs(
    client: DynatraceClient,
    query: str,
    timeframe: str | None = None,
    max_wait_seconds: int = 120,
) -> dict:
    if not (1 <= max_wait_seconds <= _MAX_WAIT_SECONDS):
        raise ValueError(
            f"max_wait_seconds must be between 1 and {_MAX_WAIT_SECONDS}, got {max_wait_seconds}."
        )

    query = _enrich_query(query)
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
    deadline = time.monotonic() + max_wait_seconds
    # Always poll at least once before the deadline.
    while True:
        try:
            data = await _poll_with_retry(client, request_token)
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

        if time.monotonic() >= deadline:
            break

        await asyncio.sleep(1)

    return {
        "state": "TIMEOUT",
        "message": (
            f"Query did not complete within {max_wait_seconds} seconds. "
            f"Call poll_query immediately with request_token '{request_token}' to retrieve results."
        ),
        "metadata": {
            "request_token": request_token,
            "progress": data.get("progress") if data else None,
        },
    }


async def poll_query(client: DynatraceClient, request_token: str) -> dict:
    try:
        data = await _poll_with_retry(client, request_token)
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
