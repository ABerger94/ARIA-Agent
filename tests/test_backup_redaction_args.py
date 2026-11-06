"""Pass 26 B3#7: backup redaction must not leak credentials hiding
in MCP args lists and URLs.

Verified against the code at b1ba4ad:
- backup_mirror.py _redact_settings() redacts only env/headers
  keys and credential-named keys; MCP configs (mcp.py:120-153)
  carry args lists and url strings, so args=["--api-key",
  "sk-..."] and URLs with access_token=... survive into the
  mirrored (synced-folder) settings.json verbatim.

Fix contract pinned here:
- a list element following a credential-like flag (--api-key,
  --token, --password, ...) is redacted, as is a --flag=value
  element and a KEY=value element with a credential-like key;
- credential query parameters (access_token, api_key, token,
  auth, ...) in URL-valued strings are replaced with the
  existing _REDACTED marker (other params untouched), as is a
  password in URL userinfo;
- non-secret values pass through untouched, as before.

Run per-file (own process), like every suite in this repo.
"""
import copy
import json
import sys
import types
from pathlib import Path

pkg = types.ModuleType('aria')
pkg.__path__ = [str(Path(__file__).parents[1] / 'aria')]
sys.modules.setdefault('aria', pkg)

from aria import backup_mirror as bm


def _dump(data):
    return json.dumps(data, sort_keys=True)


def test_mcp_args_values_after_credential_flags_redacted():
    data = {"mcp_servers": {"gh": {
        "transport": "stdio", "command": "npx",
        "args": ["-y", "--api-key", "sk-live-123", "--token", "tok-456",
                 "--verbose", "plain",
                 "--password=sekret", "--api-key=sk-also-live"]}}}
    bm._redact_settings(data)
    args = data["mcp_servers"]["gh"]["args"]
    assert args == ["-y", "--api-key", bm._REDACTED,
                    "--token", bm._REDACTED,
                    "--verbose", "plain",
                    "--password=" + bm._REDACTED,
                    "--api-key=" + bm._REDACTED], args
    assert "sk-live-123" not in _dump(data)
    assert "tok-456" not in _dump(data)
    assert "sekret" not in _dump(data)
    assert "sk-also-live" not in _dump(data)


def test_env_style_key_value_list_elements_redacted():
    data = {"mcp_servers": {"s": {"args": ["API_KEY=sk-xyz",
                                           "MODE=fast"]}}}
    bm._redact_settings(data)
    assert data["mcp_servers"]["s"]["args"] == [
        "API_KEY=" + bm._REDACTED, "MODE=fast"]


def test_credential_query_params_redacted_other_params_kept():
    data = {"mcp_servers": {"s": {
        "transport": "streamable-http",
        "url": ("https://example.com/mcp?access_token=sekret"
                "&other=keep&api_key=also-secret")}}}
    bm._redact_settings(data)
    url = data["mcp_servers"]["s"]["url"]
    assert "sekret" not in url
    assert "also-secret" not in url
    assert "other=keep" in url
    # Parse the result back: the credential params carry the
    # marker (percent-encoded in the raw string), the rest is
    # untouched.
    from urllib.parse import urlsplit as _split, parse_qsl as _qsl
    params = dict(_qsl(_split(url).query, keep_blank_values=True))
    assert params["access_token"] == bm._REDACTED, params
    assert params["api_key"] == bm._REDACTED, params
    assert params["other"] == "keep", params


def test_url_userinfo_password_redacted():
    data = {"mcp_servers": {"s": {
        "url": "https://user:hunter2@example.com/mcp"}}}
    bm._redact_settings(data)
    url = data["mcp_servers"]["s"]["url"]
    assert "hunter2" not in url
    assert "user:" in url and "example.com/mcp" in url


def test_non_secret_values_untouched():
    original = {"voice_enabled": True,
                "mcp_servers": {"s": {
                    "transport": "stdio", "command": "npx",
                    "args": ["--verbose", "hello"],
                    "url": "https://example.com/mcp?foo=bar"}}}
    data = copy.deepcopy(original)
    bm._redact_settings(data)
    assert data == original


def test_existing_env_headers_and_key_redaction_unchanged():
    data = {"mcp_servers": {"gh": {
                "env": {"GITHUB_TOKEN": "ghp_real"},
                "headers": {"Authorization": "Bearer abc"}}},
            "some_api_key": "sk-real"}
    bm._redact_settings(data)
    assert data["mcp_servers"]["gh"]["env"] == {
        "GITHUB_TOKEN": bm._REDACTED}
    assert data["mcp_servers"]["gh"]["headers"] == {
        "Authorization": bm._REDACTED}
    assert data["some_api_key"] == bm._REDACTED
