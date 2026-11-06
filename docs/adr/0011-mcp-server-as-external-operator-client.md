# ADR-0011: The MCP server is an external operator client

**Status:** Accepted · **Date:** 2026-11-06

## Context

Coding agents (Claude Code, GitHub Copilot agent mode, Claude Desktop) are increasingly the first
thing someone points at a running system when it misbehaves. Given only a shell, an agent sees
Forno the way a stranger would: it can `curl` endpoints and grep logs, but it does not know that
`IN_KITCHEN` is the state where most promises are lost, that a rising ticket age with the oven at
100% is a capacity limit rather than a bug, or that `rate()` over domain-time histograms needs
ADR-0009 in mind. It guesses, and the guesses are confidently wrong.

The knowledge exists — the runbook's triage tree, the domain model, the alert rules, the ADRs. It
is written for humans and spread over a dozen files and three data sources (API, SSE, Prometheus).

The [Model Context Protocol](https://modelcontextprotocol.io) is the standard way to hand an agent
tools, readable resources, and prompts. The question is where such a server sits.

## Decision

**`forno_mcp/` is a separate top-level package that talks to the running system only over the
public HTTP API, the SSE streams, and the Prometheus query API** — the same position as the
simulator ([ADR-0008](0008-simulator-as-external-client.md)), enforced the same way:

```ini
[importlinter:contract:mcp-is-external]
type = forbidden
source_modules = forno_mcp
forbidden_modules = forno
```

It exposes three things, following the protocol's own split:

- **Resources** — the docs an operator reads (domain model, runbook, observability, API, ADRs,
  scenarios) and the state machines as JSON. Context, not guesses.
- **Tools that observe** — a service snapshot, `diagnose` (the runbook's triage tree as code), a
  bounded watch over the SSE streams, one order explained against its promise, raw PromQL.
- **Tools that act** — run a simulator scenario against the live stack in the background and read
  its invariant report; take an oven out of service. These carry `destructive_hint` so a client
  can ask a human first.

Where the server copies knowledge it cannot import (the FSM transition tables, the alert→runbook
mapping), a test pins the copy against the source of truth: the domain model doc, `item.py`, the
alert rules, the runbook headings. Drift fails CI rather than misleading an agent.

## Consequences

1. **The triage tree becomes executable.** `diagnose` walks exactly the branches of the runbook's
   "first sixty seconds" flowchart with the thresholds from `alert_rules.yml`, and returns the
   matching runbook section verbatim. An agent and an on-call human reach the same conclusion from
   the same data.
2. **Simulations become an agent's experiment tool.** "What happens if deck-2 dies at the rush?"
   is one `start_simulation("oven-down")` call followed by reading the report — against the real
   stack, not a model of it.
3. **The API gets a second external reviewer.** Every place the server needed data the public API
   did not offer is a gap a frontend would hit too — the same feedback loop as ADR-0008.
4. **Degrades instead of failing.** Without Prometheus the snapshot and triage fall back to what the
   API can see, and say so; without the API every tool answers "start it with `make up`".

Cost: a second copy of a few tables (pinned by tests), and a separate dependency set (`mcp`,
`httpx-sse`) kept out of the application image.

## Alternatives rejected

**Expose MCP from the API process.** One fewer process, and it couples an operator tool's release
cycle and dependency tree to the request path. It would also be able to read the database directly,
which is precisely the shortcut that hides gaps in the public contract.

**Give the agent a shell and a README.** Works for reading code. For a live incident it leaves the
agent to rediscover the triage tree on every run, with no guard rails around the commands that
change state.

**Read-only server, no actions.** Safer, and it removes the most useful question an agent can
answer: "show me what this change does under load". Destructive tools are kept, annotated, and
narrow: one simulation at a time, one oven at a time, a mandatory `reason` on chaos.

## What would change this

- **A real authentication model.** Today the server uses the dev API key and `X-Role` headers, as
  the simulator does. With real auth, the server should act under the connecting human's identity,
  and the destructive tools move behind an explicit role.
- **A frontend.** If one is built, the snapshot shape should come from an API endpoint both share,
  and `take_snapshot` shrinks to a single call.
