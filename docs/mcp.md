# MCP server: Forno for coding agents

`forno_mcp/` is a [Model Context Protocol](https://modelcontextprotocol.io) server that lets a
coding agent — Claude Code, GitHub Copilot agent mode, Claude Desktop, anything that speaks MCP —
**understand what the running pizzeria is doing, explain why, and run experiments on it.**

It is an operator client of the live system: public HTTP API, SSE streams, Prometheus. It cannot
import `forno` ([ADR-0011](adr/0011-mcp-server-as-external-operator-client.md), enforced by
import-linter).

---

## What the agent gets

### Tools: observe

| Tool | Answers |
|---|---|
| `get_service_snapshot` | "What is happening right now?" — health, open orders by status, late orders, kitchen tickets, ovens, tables, SLI vs. the 95% SLO, firing alerts, and plain-sentence headlines |
| `diagnose` | "Is the software broken, or are there more customers than chefs?" — walks the [runbook](runbook.md) triage tree with the alert-rule thresholds and returns the verdict, the evidence, and the matching runbook section |
| `watch_live` | "What happens over the next N seconds?" — subscribes to the kitchen, service and floor SSE streams (≤120 s) and returns a story of the events, not a raw dump |
| `explain_order` | "Why is this order late?" — state, legal next transitions, items by stage, time left on (or past) the promise |
| `list_orders` | Orders filtered by status or table |
| `query_metric` | Any PromQL, for the questions the snapshot does not cover |
| `list_scenarios` | Simulator scenarios with their description and expected outcome bands |
| `get_simulation` / `list_simulations` | Progress, log tail and invariant report of a simulation |

### Tools: act

Marked `destructive_hint` — a well-behaved client asks the human before calling them.

| Tool | Effect |
|---|---|
| `start_simulation` | Runs a scenario (`busy-friday`, `understaffed`, `oven-down`, …) **against the live stack** in the background; returns a `job_id` at once |
| `stop_simulation` | Stops it early; the report still covers what it saw |
| `set_oven_active` | Takes an oven out of service or brings it back, with a mandatory `reason` — the same management call the `oven-down` scenario makes |

Guard rails, all enforced in code:

- **One simulation at a time.** Two simulators sharing a pizzeria would corrupt each other's shadow
  ledgers.
- **Time scale.** The live API runs at one `time_scale` ([ADR-0009](adr/0009-scaled-domain-time.md));
  the simulator refuses a scenario at another. By default the server runs a copy of the scenario
  re-timed to the API's scale and says so in the job's `notes`; with `match_api_time_scale=false`
  it refuses instead.
- **Honest notes.** A run that would take more than 15 wall-clock minutes, or that starts with
  orders already `IN_KITCHEN` holding oven slots, is flagged before it starts.

### Resources

| URI | Content |
|---|---|
| `forno://docs/{domain-model,runbook,observability,api,simulator,architecture,configuration}` | The docs in this folder |
| `forno://adr`, `forno://adr/{id}` | ADR index and one ADR, e.g. `forno://adr/0002` |
| `forno://scenarios/{name}` | A scenario file |
| `forno://state-machines` | Order and item FSMs as JSON, with what each order state means |

### Prompts

| Prompt | Runs |
|---|---|
| `incident-triage` | Snapshot → diagnose → runbook → a short incident note |
| `why-is-the-kitchen-slow` | Snapshot → late orders explained → capacity vs. software verdict |
| `run-experiment` | Snapshot before → simulation → report → snapshot after → what changed |

---

## Running it

Prerequisites: [uv](https://docs.astral.sh/uv/) and a running stack (`make up`). The server
starts without one — every tool then answers "start it with `make up`".

```bash
make mcp          # stdio — what agents spawn themselves; run by hand only to check it starts
make mcp-http     # Streamable HTTP on http://127.0.0.1:8765/mcp
make test-mcp     # mypy strict + tests (fake API, real in-process MCP client)
```

`start_simulation` runs the simulator on the host with `uv run --project simulator`, so the API
must be reachable from the host — which it is under `make up` (`localhost:8000`).

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FORNO_MCP_API_URL` | `http://localhost:8000` | Forno API |
| `FORNO_MCP_API_KEY` | `dev` | Sent as `X-API-Key`, as the simulator does |
| `FORNO_MCP_PROMETHEUS_URL` | `http://localhost:9090` | Optional; without it, snapshot and triage fall back to API data and say so |
| `FORNO_MCP_REPO_ROOT` | the repository | Where docs, ADRs and scenarios are read from |
| `FORNO_MCP_SIMULATOR_CMD` | `uv run --project simulator --quiet python -m simulator` | Command for `start_simulation` |

---

## Connecting an agent

### Claude Code

The repository ships a project-scoped [`.mcp.json`](../.mcp.json); opening the repo in Claude Code
offers to enable the `forno` server. To add it by hand instead:

```bash
claude mcp add forno -- uv run --project forno_mcp --quiet python -m forno_mcp
```

Then ask, for example: *"Is the pizzeria healthy? If not, why?"* or *"Run oven-down and tell me
what broke first."*

### GitHub Copilot (VS Code agent mode)

`.vscode/mcp.json`:

```json
{
  "servers": {
    "forno": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--project", "forno_mcp", "--quiet", "python", "-m", "forno_mcp"]
    }
  }
}
```

### Claude Desktop

`claude_desktop_config.json` — Desktop does not start in the repository, so give absolute paths:

```json
{
  "mcpServers": {
    "forno": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/forno/forno_mcp", "--quiet",
               "python", "-m", "forno_mcp"],
      "env": { "FORNO_MCP_REPO_ROOT": "/path/to/forno" }
    }
  }
}
```

### Any client over HTTP

`make mcp-http`, then point the client at `http://127.0.0.1:8765/mcp`. `--host` and `--port`
change the address; keep it on localhost — the destructive tools have no authentication of their
own beyond the dev API key.

---

## Logging

Every tool call is one JSON line on **stderr** (`tool_call` with tool, duration and outcome; plus
`oven_toggled` with the reason). Stdout carries the protocol under stdio, so nothing else is ever
printed there.

## How it is tested

No containers. `forno_mcp/tests/fakes.py` is a fake Forno API (including SSE bodies) and a fake
Prometheus behind `httpx.MockTransport`; the tests connect a **real MCP client** to the server
in-process and call tools as an agent would. The knowledge the server copies from the
application — FSM transition tables, the alert → runbook mapping — is pinned by tests against
`docs/domain-model.md`, `src/forno/domain/item.py`, `deploy/prometheus/alert_rules.yml` and the
runbook headings, so drift fails CI rather than misleading an agent.

What is not covered yet: a run against the real compose stack in CI (the `system-invariants` job
is the natural place for it).
