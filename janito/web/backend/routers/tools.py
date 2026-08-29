"""Tool introspection endpoints."""

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def list_tools(request: Request):
    """List the tools offered in this session + schemas + permissions."""
    from janito import privileges as _privileges_mod
    from janito.tooling.tools_registry import (
        get_all_tool_permissions,
        get_all_tools,
        get_session_tool_names,
        get_session_tool_schemas,
        tools_loading_enabled,
    )
    from janito.tools import privilege_restriction_reason

    # The session tool set: privilege-filtered under -r/-w/-x (issue #87).
    schemas = get_session_tool_schemas()
    permissions = get_all_tool_permissions()
    all_tools = get_all_tools()
    session_names = get_session_tool_names()

    tools = []
    for schema in schemas:
        fn = schema.get("function", {})
        name = fn.get("name", "")
        tools.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "permissions": permissions.get(name, ""),
                "parameters": fn.get("parameters", {}),
            }
        )

    # Loaded but excluded by the session privileges; the /read /write /rx
    # /rw /rwx overrides can still offer them (issue #87).
    privilege_restricted = []
    if _privileges_mod.running_privileges is not None:
        for name in sorted(all_tools):
            if name not in session_names:
                privilege_restricted.append(
                    {
                        "name": name,
                        "permissions": permissions.get(name, ""),
                        "reason": privilege_restriction_reason(
                            permissions.get(name, "")
                        )
                        or "restricted by session privileges",
                    }
                )

    return {
        "tools": tools,
        "count": len(tools),
        "privilege_restricted": privilege_restricted,
        "tools_enabled": tools_loading_enabled(),
    }


@router.get("/skipped")
async def list_skipped_tools(request: Request):
    """Tools skipped during discovery + reasons."""
    from janito.tools import get_skipped_tools

    return {"skipped": get_skipped_tools()}


@router.post("/toolsets/{name}")
async def add_toolset(name: str, request: Request):
    """Dynamically add a toolset (janitoweb...)."""
    from janito.tooling.tools_registry import add_toolset as _add_toolset

    ok = _add_toolset(name)
    return {"toolset": name, "added": ok}
