"""Entry point: `python -m forno_mcp` (stdio, for a local agent) or
`python -m forno_mcp --http` (Streamable HTTP). Run from the repo root.

    FORNO_MCP_API_URL         default http://localhost:8000
    FORNO_MCP_PROMETHEUS_URL  default http://localhost:9090
    FORNO_MCP_API_KEY         default dev (the stand-in key, docs/api.md)
    FORNO_MCP_SIMULATOR_CMD   default `uv run --project simulator --quiet python -m simulator`
    FORNO_MCP_REPO_ROOT       default this repository (docs, ADRs, scenarios)
"""

from __future__ import annotations

import argparse

from forno_mcp.server import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="forno_mcp", description=__doc__)
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP, not stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.http:
        run("streamable-http", host=args.host, port=args.port)
    else:
        run("stdio")


if __name__ == "__main__":
    main()
