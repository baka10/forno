"""MCP server for operating a live Forno pizzeria from an AI agent.

An external client in exactly the sense of docs/adr/0008: it talks to the
public HTTP API, the SSE streams and Prometheus, never to `forno` itself.
See docs/adr/0011-mcp-server-as-external-operator-client.md and docs/mcp.md.
"""
