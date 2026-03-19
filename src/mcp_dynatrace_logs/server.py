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
    max_wait_seconds: int = 30,
) -> dict:
    """
    Execute a DQL query against Dynatrace logs and return results.

    Args:
        query: Full DQL string, e.g. "fetch logs | filter context=\"*xxx*\" | limit 100"
        timeframe: Optional time offset like "3d" or "1h". Added as defaultTimeframeStart.
        max_wait_seconds: How long to poll before returning a TIMEOUT state (default 30).

    Returns a dict with "state" key: SUCCEEDED, FAILED, TIMEOUT, or ERROR.
    On TIMEOUT, use poll_query with the returned request_token to retrieve results later.
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
    # Fail fast at startup if credentials are missing
    _get_client()
    mcp.run()


if __name__ == "__main__":
    main()
