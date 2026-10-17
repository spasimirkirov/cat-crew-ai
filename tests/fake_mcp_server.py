"""A small real MCP server for the MCP tests (official ``mcp`` SDK, v2 API).

    python tests/fake_mcp_server.py stdio
    python tests/fake_mcp_server.py streamable-http 8765
    python tests/fake_mcp_server.py sse 8766

Environment: FAKE_FLAG=<file> makes the ``bridge`` tool fail while that file exists (the "program behind
the server is closed" case); FAKE_MANY=<n> registers n extra tools ``bulk_00`` ... to test the tool cap.
"""

from __future__ import annotations

import base64
import os
import sys

from mcp.server import MCPServer
from mcp.server.mcpserver.utilities.types import Image
from mcp.types import ToolAnnotations

PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
mcp = MCPServer("fake")

_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
def peek(what: str = "state") -> str:
    """Read-only look at something."""
    return f"peek:{what}"


@mcp.tool()
def boom() -> str:
    """Always fails."""
    raise ValueError("kaboom")


@mcp.tool()
def snap() -> Image:
    """Return a PNG."""
    return Image(data=_PNG, format="png")


@mcp.tool()
def bridge() -> str:
    """Cheap probe: fails while the FAKE_FLAG file exists."""
    flag = os.environ.get("FAKE_FLAG", "")
    if flag and os.path.exists(flag):
        raise RuntimeError("the program is closed")
    return "ok"


@mcp.tool()
def die() -> str:
    """Kill the server process."""
    os._exit(3)


@mcp.tool()
def grow(name: str = "late_tool") -> str:
    """Add a tool while running."""

    def late() -> str:
        """Added at run time."""
        return "late"

    mcp.add_tool(late, name=name)
    return "added " + name


@mcp.tool()
def refresh_unity(mode: str = "if_dirty", scope: str = "all", compile: str = "none", wait_for_ready: bool = False) -> str:
    """Pretend to refresh and compile."""
    return f"refreshed:{mode}:{scope}:{compile}"


@mcp.tool()
def read_console(action: str = "get", types: list[str] | None = None, count: int = 10, format: str = "plain") -> str:
    """Pretend Unity console."""
    path = os.environ.get("FAKE_CONSOLE", "")
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return "Nothing in the console."


@mcp.resource("fake://state")
def state() -> str:
    """A resource, for the probe."""
    flag = os.environ.get("FAKE_FLAG", "")
    if flag and os.path.exists(flag):
        raise RuntimeError("no editor")
    return "state ok"


for _i in range(int(os.environ.get("FAKE_MANY", "0"))):

    def _make(i: int):
        def bulk() -> str:
            f"""Bulk tool number {i}."""
            return f"bulk {i}"

        bulk.__doc__ = f"Bulk tool number {i}."
        return bulk

    mcp.add_tool(_make(_i), name=f"bulk_{_i:02d}")

if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if kind == "stdio":
        mcp.run("stdio")
    elif kind == "sse":
        mcp.run("sse", host="127.0.0.1", port=PORT)
    else:
        mcp.run("streamable-http", host="127.0.0.1", port=PORT, streamable_http_path="/mcp")
