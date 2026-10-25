"""Schema integrity checks for the MCP server.

The tool schemas are an agent-facing contract, so they must not drift from the handlers that
read them. These checks are pure (no network) and run in the test suite:

- `validate_tool_schemas`: structural validity of every advertised tool
- `handler_argument_names`: which arguments `execute_mcp_tool` actually reads for a tool,
  extracted from its own source, so an advertised-but-ignored parameter fails the build
"""

from __future__ import annotations

import ast
import functools
import inspect
from typing import Any

JSON_SCHEMA_TYPES: frozenset[str] = frozenset(
    {"string", "number", "integer", "boolean", "object", "array", "null"}
)


@functools.lru_cache(maxsize=1)
def _handler_blocks() -> dict[str, ast.If]:
    """Map each tool name to the `if name == "...":` block that implements it."""
    from PyNCBI.mcp import server

    source = inspect.getsource(server.execute_mcp_tool)
    tree = ast.parse(ast.unparse(ast.parse(source)))  # normalize indentation
    func = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    blocks: dict[str, ast.If] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "name"
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and isinstance(test.comparators[0].value, str)
        ):
            blocks[test.comparators[0].value] = node
    return blocks


def handler_argument_names(tool_name: str) -> set[str]:
    """Argument names the handler for `tool_name` reads from its `arguments` mapping."""
    block = _handler_blocks().get(tool_name)
    if block is None:
        return set()
    found: set[str] = set()
    for node in ast.walk(block):
        # _argument_text(arguments, "x") / _argument_text(arguments, "x", default)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_argument_text"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "arguments"
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            found.add(node.args[1].value)
        # arguments.get("x") / arguments.get("x", default)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "arguments"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
        # arguments["x"]
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "arguments"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            found.add(node.slice.value)
    return found


def validate_tool_schemas(tools: list[dict[str, Any]] | None = None) -> list[str]:
    """Return a list of human-readable problems with the advertised tool schemas (empty = valid)."""
    from PyNCBI.mcp.server import MCP_TOOLS

    checked = MCP_TOOLS if tools is None else tools
    problems: list[str] = []
    seen: set[str] = set()

    for tool in checked:
        name = str(tool.get("name", "<unnamed>"))
        if name in seen:
            problems.append(f"{name}: duplicate tool name")
        seen.add(name)
        if not str(tool.get("description", "")).strip():
            problems.append(f"{name}: missing description")
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            problems.append(f"{name}: missing inputSchema")
            continue
        if schema.get("type") != "object":
            problems.append(f"{name}: inputSchema type must be 'object'")
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            problems.append(f"{name}: inputSchema has no properties object")
            continue
        unknown_required = set(schema.get("required", [])) - set(properties)
        if unknown_required:
            problems.append(f"{name}: required names not in properties: {sorted(unknown_required)}")
        for prop, spec in properties.items():
            if not isinstance(spec, dict):
                problems.append(f"{name}.{prop}: property must be an object")
                continue
            if not str(spec.get("description", "")).strip():
                problems.append(f"{name}.{prop}: missing description")
            declared = spec.get("type")
            if declared is not None and declared not in JSON_SCHEMA_TYPES:
                problems.append(f"{name}.{prop}: unknown JSON Schema type {declared!r}")
            if declared == "array" and "items" not in spec:
                problems.append(f"{name}.{prop}: array property without 'items'")
            default = spec.get("default")
            if default is not None and declared == "integer" and not isinstance(default, int):
                problems.append(f"{name}.{prop}: default {default!r} is not an integer")
        annotations = tool.get("annotations")
        if tools is None:  # annotations are attached by the server, not by ad-hoc callers
            if not isinstance(annotations, dict):
                problems.append(f"{name}: missing annotations")
            elif not isinstance(annotations.get("readOnlyHint"), bool):
                problems.append(f"{name}: annotations.readOnlyHint must be a boolean")
    return problems
