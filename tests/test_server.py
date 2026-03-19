import os
import pytest
from unittest.mock import patch


def test_missing_dynatrace_url_raises():
    with patch.dict(os.environ, {"DYNATRACE_API_TOKEN": "token"}, clear=True):
        with pytest.raises(EnvironmentError, match="DYNATRACE_URL"):
            from mcp_dynatrace_logs.server import _build_client
            _build_client()


def test_missing_dynatrace_token_raises():
    with patch.dict(os.environ, {"DYNATRACE_URL": "https://test.dynatrace.com"}, clear=True):
        with pytest.raises(EnvironmentError, match="DYNATRACE_API_TOKEN"):
            from mcp_dynatrace_logs.server import _build_client
            _build_client()


def test_both_env_vars_present_returns_client():
    env = {
        "DYNATRACE_URL": "https://test.dynatrace.com",
        "DYNATRACE_API_TOKEN": "mytoken",
    }
    with patch.dict(os.environ, env, clear=True):
        from mcp_dynatrace_logs.server import _build_client
        client = _build_client()
        assert client is not None
