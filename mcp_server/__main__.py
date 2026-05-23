"""Entrypoint:
    python -m mcp_server                 stdio (for Claude Desktop, Cursor, etc.)
    python -m mcp_server --http          streamable HTTP on :8765
    python -m mcp_server --http --port N streamable HTTP on :N
"""
from __future__ import annotations

import argparse
import os
import sys

from mcp_server.server import mcp


def main() -> None:
    ap = argparse.ArgumentParser(prog="mcp_server")
    ap.add_argument("--http", action="store_true",
                    help="serve streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("MCP_PORT", "8765")))
    ap.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    args = ap.parse_args()

    if args.http:
        # FastMCP's streamable HTTP transport. Compatible with MCP clients
        # that speak the streamable-http profile.
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        print(f"reportitnow MCP serving on http://{args.host}:{args.port}/mcp",
              file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
