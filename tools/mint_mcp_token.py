"""Mint a FastAPI session token for one of the demo users and print a ready-
to-paste Claude Desktop config block.

    python -m tools.mint_mcp_token complainant@demo.local
    python -m tools.mint_mcp_token po@demo.local --password demo
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx


def main() -> None:
    ap = argparse.ArgumentParser(prog="mint_mcp_token")
    ap.add_argument("email")
    ap.add_argument("--password", default="demo")
    ap.add_argument("--api", default=os.environ.get(
        "REPORTITNOW_API", "http://127.0.0.1:8000"))
    args = ap.parse_args()

    try:
        r = httpx.post(f"{args.api}/auth/login",
                       json={"email": args.email, "password": args.password},
                       timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        print(f"login failed: {e}", file=sys.stderr)
        sys.exit(1)

    data = r.json()
    token = data["token"]
    roles = data["roles"]

    print(f"# Logged in as {data['email']} (roles: {', '.join(roles)})")
    print(f"# Token expires in ~8h. Re-run this script to refresh.\n")
    print(f"export REPORTITNOW_TOKEN={token}\n")
    print("# --- Claude Desktop config (~/Library/Application Support/Claude/claude_desktop_config.json):")
    print("# {")
    print('#   "mcpServers": {')
    print('#     "reportitnow": {')
    print('#       "command": "python",')
    print('#       "args": ["-m", "mcp_server"],')
    print(f'#       "cwd": "{os.getcwd()}",')
    print('#       "env": {')
    print(f'#         "REPORTITNOW_API": "{args.api}",')
    print(f'#         "REPORTITNOW_TOKEN": "{token}"')
    print('#       }')
    print('#     }')
    print('#   }')
    print('# }')


if __name__ == "__main__":
    main()
