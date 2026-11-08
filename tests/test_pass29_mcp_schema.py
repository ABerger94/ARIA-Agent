"""MCP schema conversion must not misrepresent nullable / referenced
params as empty OBJECTs (pass 29 #55).

`type: ["string", "null"]` used to stringify to "['string',
'null']", miss _SCHEMA_TYPES, and fall back to OBJECT; a $ref
was never consulted, so a referenced schema became type
"object" with no properties. convert_schema now normalizes a
type list to its single non-null member, resolves bounded local
$refs, and raises ValueError on a shape it cannot represent —
never a silent empty OBJECT.
"""

import importlib
import os
import sys
import tempfile
import types
from types import SimpleNamespace

import pytest

_TMPHOME = tempfile.mkdtemp(prefix="aria_pass29_schema_")
os.environ["HOME"] = _TMPHOME

PKG = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "aria")
pkg = types.ModuleType("aria")
pkg.__path__ = [PKG]
sys.modules["aria"] = pkg
from aria import data_dir as _data_dir  # noqa: E402
_data_dir._ROOT = os.path.join(_TMPHOME, "ARIA")

# --- fake aria.tools.schemas / aria.tools.dispatch (registration test) --
schemas = types.ModuleType("aria.tools.schemas")
schemas.registered = {}


def _reg_decl(name, decl, toolkit=None):
    schemas.registered[name] = decl


def _unreg_decl(name, toolkit=None):
    schemas.registered.pop(name, None)


schemas.register_dynamic_tool_declaration = _reg_decl
schemas.unregister_dynamic_tool_declaration = _unreg_decl

dispatch = types.ModuleType("aria.tools.dispatch")
dispatch._REGISTRY = {}


def _register_tool(name, handler, **kwargs):
    dispatch._REGISTRY[name] = handler


dispatch.register_tool = _register_tool

tools_pkg = types.ModuleType("aria.tools")
tools_pkg.__path__ = [os.path.join(PKG, "tools")]
tools_pkg.schemas = schemas
tools_pkg.dispatch = dispatch
sys.modules["aria.tools"] = tools_pkg
sys.modules["aria.tools.schemas"] = schemas
sys.modules["aria.tools.dispatch"] = dispatch

mcp = importlib.import_module("aria.mcp")


def test_nullable_type_list_normalizes():
    assert mcp.convert_schema({"type": ["string", "null"]})["type"] == "STRING"
    assert mcp.convert_schema({"type": ["null", "integer"]})["type"] == "INTEGER"
    assert mcp.convert_schema(
        {"type": "array", "items": {"type": ["boolean", "null"]}}
    )["items"]["type"] == "BOOLEAN"
    print("ok nullable_type_list")


def test_nullable_inside_object_properties():
    conv = mcp.convert_schema({
        "type": "object",
        "properties": {
            "name": {"type": ["string", "null"], "description": "who"},
            "count": {"type": "integer"},
        },
        "required": ["name"],
    })
    assert conv["properties"]["name"]["type"] == "STRING"
    assert conv["properties"]["name"]["description"] == "who"
    assert conv["properties"]["count"]["type"] == "INTEGER"
    assert conv["required"] == ["name"]
    print("ok nullable_in_properties")


def test_local_ref_resolved_defs():
    schema = {
        "type": "object",
        "properties": {
            "addr": {"$ref": "#/$defs/Address"},
            "name": {"type": ["string", "null"]},
        },
        "required": ["addr"],
        "$defs": {
            "Address": {
                "type": "object",
                "properties": {"street": {"type": "string"}},
                "required": ["street"],
            },
        },
    }
    conv = mcp.convert_schema(schema)
    addr = conv["properties"]["addr"]
    assert addr["type"] == "OBJECT", addr
    assert addr["properties"]["street"]["type"] == "STRING", addr
    assert addr["required"] == ["street"], addr
    assert conv["properties"]["name"]["type"] == "STRING"
    print("ok local_ref_defs")


def test_local_ref_resolved_definitions_and_top_level():
    schema = {
        "$ref": "#/definitions/Thing",
        "definitions": {
            "Thing": {"type": "string", "description": "a thing"},
        },
    }
    conv = mcp.convert_schema(schema)
    assert conv == {"type": "STRING", "description": "a thing"}, conv
    # A $ref node's own description wins only when the target has none.
    schema2 = {
        "$ref": "#/$defs/Bare",
        "description": "from the ref node",
        "$defs": {"Bare": {"type": "integer"}},
    }
    assert mcp.convert_schema(schema2) == {
        "type": "INTEGER", "description": "from the ref node"}
    print("ok local_ref_definitions_top_level")


def test_unsupported_shapes_raise_not_empty_object():
    bad = [
        {"type": ["string", "number"]},
        {"type": ["null"]},
        {"type": []},
        {"type": "null"},
        {"type": "fnord"},
        {"$ref": "#/definitions/Missing"},
        {"$ref": "https://example.com/schema.json"},
        {"$ref": "#/$defs/Nope", "$defs": {}},
        {"$ref": "#/properties/name"},
    ]
    for node in bad:
        with pytest.raises(ValueError):
            mcp.convert_schema(node)
    print("ok unsupported_shapes_raise")


def test_cyclic_ref_raises_not_recursion_error():
    schema = {
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/A"}},
        "$defs": {"A": {"$ref": "#/$defs/A"}},
    }
    with pytest.raises(ValueError):
        mcp.convert_schema(schema)
    with pytest.raises(ValueError):
        mcp.convert_schema({"$ref": "#"})
    print("ok cyclic_ref_raises")


def test_missing_schema_still_object():
    assert mcp.convert_schema(None) == {"type": "OBJECT"}
    assert mcp.convert_schema({})["type"] == "OBJECT"
    assert mcp.convert_schema({"type": "object"})["type"] == "OBJECT"
    print("ok missing_schema_object")


def test_registration_refuses_bad_schema_tool_keeps_others():
    schemas.registered.clear()
    dispatch._REGISTRY.clear()
    tools = [
        SimpleNamespace(name="good", description="good",
                        inputSchema={"type": "object",
                                     "properties": {"x": {"type": "string"}}}),
        SimpleNamespace(name="bad", description="bad",
                        inputSchema={"type": "object",
                                     "properties": {
                                         "y": {"type": ["string", "number"]}}}),
    ]
    names = mcp._register_server_tools("srv", tools)
    assert names == ["mcp_srv__good"], names
    assert "mcp_srv__bad" not in dispatch._REGISTRY
    assert schemas.registered["mcp_srv__good"]["parameters"]["properties"][
        "x"]["type"] == "STRING"
    print("ok registration_refuses_bad_schema")
