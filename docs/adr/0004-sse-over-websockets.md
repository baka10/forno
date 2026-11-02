# ADR-0004: SSE, not WebSockets

**Status:** Accepted · **Date:** 2026-01-16

## Context

Kitchen displays and waiter clients need live updates. The reflexive choice is WebSockets.

Examined honestly, all three streams are **strictly server→client**. Every client-to-server
interaction in this system is a command with a meaningful response, a status code, an idempotency
key and an authorisation check — in other words, an HTTP request.

## Decision

**Server-Sent Events** for all live streams. Commands stay on REST.

## Consequences

- Reconnection is built into the browser, including `Last-Event-ID` replay. A WebSocket
  implementation means writing that reconnect-and-resume logic by hand, in every client.
- It is plain HTTP: proxies, load balancers, HTTP/2 multiplexing, `curl` and browser devtools all
  work with no special handling. `curl -N localhost:8000/streams/kitchen` is a debugging session.
- Authentication and authorisation reuse the same dependency as every other route. WebSocket auth is
  its own puzzle, typically solved badly with a token in a query string.
- No connection state means a command's `409` is delivered as a `409`, not as an error frame the
  client has to correlate back to a request it sent.
- Cost: text only, one direction, and browsers cap connections per origin on HTTP/1.1 (irrelevant
  over HTTP/2).

## Alternatives rejected

**WebSockets.** The right answer when the client genuinely pushes at high frequency — collaborative
editing, multiplayer, live cursors. Adopting it here would mean re-implementing request/response
semantics inside a socket in order to get back the status codes, idempotency and retry behaviour
that HTTP already provides.

**Polling.** Simple, and defensible at this scale. Rejected because a kitchen display is exactly the
case where seconds of staleness are visible to a human holding a pizza.

**gRPC streaming.** Better typing, worse browser story, and an extra proxy layer.

## What would change this

A client that needs to push at high frequency — a chef dragging tickets on a live board with
optimistic ordering shared between stations. That is bidirectional and would justify a socket for
that one surface.
