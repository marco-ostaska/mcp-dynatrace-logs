import os
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp_dynatrace_logs.client import DynatraceClient
from mcp_dynatrace_logs import tools

load_dotenv()

mcp = FastMCP("dynatrace-logs")

_client: DynatraceClient | None = None


def _build_client() -> DynatraceClient:
    """Build a DynatraceClient from environment variables. Raises EnvironmentError if vars are missing."""
    url = os.environ.get("DYNATRACE_URL")
    token = os.environ.get("DYNATRACE_API_TOKEN")
    if not url:
        raise EnvironmentError("DYNATRACE_URL environment variable is not set.")
    if not token:
        raise EnvironmentError("DYNATRACE_API_TOKEN environment variable is not set.")
    return DynatraceClient(base_url=url, token=token)


def _get_client() -> DynatraceClient:
    """Return the module-level client, initializing it on first call."""
    global _client
    if _client is None:
        _client = _build_client()
    return _client


@mcp.tool()
async def fetch_logs(
    query: str,
    timeframe: str | None = None,
    max_wait_seconds: int = 120,
) -> dict:
    """
    Execute a DQL query against Dynatrace logs and return results.

    Args:
        query: Full DQL string. DQL SYNTAX RULES:
               - Always start with: fetch logs
               - Pipe each operation: fetch logs | filter ... | sort ... | limit ...
               - ALWAYS use caseSensitive: false in contains(): contains(field, "value", caseSensitive: false)
               - Use matches(field, "pattern") for regex
               - Combine filters: | filter field1 == "value" AND field2 == "value"
               - Sort (NO 'by' keyword): | sort timestamp desc
               - Limit: | limit 1000  (default — do NOT exceed 1000 unless user explicitly asks for more)
               - Common fields: content, severity, status, timestamp

               Examples:
               - "fetch logs | filter contains(content, \"error\", caseSensitive: false) | sort timestamp desc | limit 1000"
               - "fetch logs | filter severity == \"ERROR\" AND contains(content, \"order-id\", caseSensitive: false) | sort timestamp desc | limit 1000"

               NOTE: The server automatically enriches your query to extract a readable
               'Log message' field from JSON and key=value log formats, unless your query
               already contains 'fieldsAdd'.

        timeframe: Optional time offset like "3d", "1h", "30m". Adds defaultTimeframeStart.
        max_wait_seconds: How long to poll before returning TIMEOUT (default 120).

    Returns a dict with "state": SUCCEEDED, FAILED, TIMEOUT, or ERROR.
    On TIMEOUT, call poll_query immediately with the returned request_token.
    """
    return await tools.fetch_logs(_get_client(), query=query, timeframe=timeframe, max_wait_seconds=max_wait_seconds)


@mcp.tool()
async def poll_query(request_token: str) -> dict:
    """
    Poll a Dynatrace query by request token. Use after fetch_logs returns TIMEOUT.

    Args:
        request_token: The token returned by a previous fetch_logs or poll_query call.

    Returns a dict with "state" key: RUNNING, SUCCEEDED, FAILED, or ERROR.
    """
    return await tools.poll_query(_get_client(), request_token=request_token)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
