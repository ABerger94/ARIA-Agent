"""Pass 27 #17: backup redaction must scrub credential-bearing URLs
inside non-credential --flag=value assignments.

The --flag=value branch checked whether the flag itself named a
credential and then continued unconditionally — so a non-credential
flag carrying a URL (--endpoint=https://example.test/mcp?access_token=SECRET)
skipped the URL scrub below it and the secret mirrored verbatim.

Fix contract pinned here:
- --flag=<url> with a credential query param or userinfo is
  scrubbed, with the flag and the non-secret URL parts intact;
- a credential flag (--api-key=x) still redacts the whole value;
- a plain --flag=value with no URL passes through untouched.
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


def _args(*items):
    return {"mcp_servers": {"s": {"args": list(items)}}}


def test_endpoint_url_credential_param_scrubbed():
    data = _args("--endpoint=https://example.test/mcp?access_token=AUDIT_SECRET")
    bm._redact_settings(data)
    arg = data["mcp_servers"]["s"]["args"][0]
    assert "AUDIT_SECRET" not in arg, arg
    assert arg.startswith("--endpoint=https://example.test/mcp?"), arg
    # The marker is URL-encoded by urlencode — assert the param is
    # present but no longer carries the secret.
    assert "access_token=" in arg, arg
    assert "access_token=AUDIT_SECRET" not in arg, arg
    assert "AUDIT_SECRET" not in _dump(data)


def test_endpoint_url_userinfo_scrubbed():
    data = _args("--endpoint=https://user:hunter2@example.test/mcp")
    bm._redact_settings(data)
    arg = data["mcp_servers"]["s"]["args"][0]
    assert "hunter2" not in arg, arg
    assert arg.startswith("--endpoint=https://"), arg


def test_credential_flag_still_redacts_whole_value():
    data = _args("--api-key=https://example.test/?token=x")
    bm._redact_settings(data)
    assert data["mcp_servers"]["s"]["args"] == ["--api-key=" + bm._REDACTED]


def test_plain_flag_value_untouched():
    data = _args("--endpoint=https://example.test/mcp",
                 "--retries=3", "--verbose")
    before = copy.deepcopy(data)
    bm._redact_settings(data)
    assert data == before, data
