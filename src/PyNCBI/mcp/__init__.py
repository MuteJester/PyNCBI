"""MCP server package for PyNCBI.

Names are resolved lazily (PEP 562). Importing this package must not execute
:mod:`PyNCBI.mcp.server`, because that module is also launched directly as
``python -m PyNCBI.mcp.server``; an eager import here would make runpy run it a
second time as ``__main__``, leaving two copies of the tool catalogue and session
state in memory and printing a RuntimeWarning on the agent's stderr.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - these exist for type checkers only
    # ruff: noqa: F401  (`__all__` is built from `_EXPORTS`, so ruff cannot see the re-exports)
    from PyNCBI.mcp.handlers import execute_mcp_tool
    from PyNCBI.mcp.prompts import MCP_PROMPTS, get_prompt
    from PyNCBI.mcp.protocol import (
        SUPPORTED_PROTOCOL_VERSIONS,
        handle_jsonrpc_message,
        mcp_manifest,
    )
    from PyNCBI.mcp.resources import (
        MCP_RESOURCE_TEMPLATES,
        MCP_RESOURCES,
        read_resource,
    )
    from PyNCBI.mcp.tools import ENVELOPE_OUTPUT_SCHEMA, MCP_TOOLS
    from PyNCBI.mcp.transport import create_http_server, run_http_server, run_mcp_server
    from PyNCBI.mcp.validation import handler_argument_names, validate_tool_schemas

# Exported name -> the module that defines it. No exported name may equal a submodule
# name: importing a submodule binds it on the package, which would shadow `__getattr__`
# and make the attribute mean different things before and after that import.
_EXPORTS: dict[str, str] = {
    "ENVELOPE_OUTPUT_SCHEMA": "PyNCBI.mcp.tools",
    "MCP_TOOLS": "PyNCBI.mcp.tools",
    "MCP_PROMPTS": "PyNCBI.mcp.prompts",
    "get_prompt": "PyNCBI.mcp.prompts",
    "MCP_RESOURCES": "PyNCBI.mcp.resources",
    "MCP_RESOURCE_TEMPLATES": "PyNCBI.mcp.resources",
    "read_resource": "PyNCBI.mcp.resources",
    "execute_mcp_tool": "PyNCBI.mcp.handlers",
    "SUPPORTED_PROTOCOL_VERSIONS": "PyNCBI.mcp.protocol",
    "handle_jsonrpc_message": "PyNCBI.mcp.protocol",
    "mcp_manifest": "PyNCBI.mcp.protocol",
    "create_http_server": "PyNCBI.mcp.transport",
    "run_http_server": "PyNCBI.mcp.transport",
    "run_mcp_server": "PyNCBI.mcp.transport",
    "handler_argument_names": "PyNCBI.mcp.validation",
    "validate_tool_schemas": "PyNCBI.mcp.validation",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Import the defining module on first access (PEP 562)."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    return __all__
