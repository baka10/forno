# docs/media

Diagram sources and their rendered exports, referenced from the README, `docs/`, and
`CONTRIBUTING.md`. Every `.drawio` file here is the source of truth; the `.png` next to it is a
rendered export, embedded with the full diagram XML (`-e` / `--embed-diagram`) so opening the PNG
directly in draw.io recovers the editable diagram.

| File | Used in | What it is |
|---|---|---|
| `architecture-c4.drawio` (2 pages) | README, `architecture.md` | C4 System Context + Containers, hand-authored via `c4.py`. Click the Forno box on page 1 to drill into page 2. |
| `deployment-topology.drawio` | `architecture.md` | **Generated from `docker-compose.yml`** by `generate-deployment-diagram.py` in this directory — service names, images, commands and profile grouping come from parsing the real compose file, not a hand-maintained picture. |
| `layering.drawio` | `architecture.md` | The 6-layer dependency diagram, with the two `FORBIDDEN` domain-purity edges styled red/dashed. |
| `reliability-machinery.drawio` | `architecture.md` | Write side / publish side / consume side + DLQ, hand-built. |
| `streaming-to-clients.drawio` | `architecture.md` | SSE fan-out to the three streams. Event names are the literal ones `adapters/messaging/sse_fanout.py`'s `_routes()` emits — verified against that function, not copied from an earlier (stale) version of this diagram. |
| `order-sequence.drawio` | `architecture.md` | "Placing an order" critical path, built via `seqlayout.py`. Two `alt` branches from the original design (idempotency replay, duplicate-delivery skip) are notes rather than frames — fragments are out of `seqlayout.py`'s scope. |
| `aggregates-er.drawio` | `domain-model.md` | ER diagram, hand-built to match `src/forno/adapters/db/migrations/versions/0001_initial_schema.py`, scoped to the domain aggregates (the outbox/idempotency/stats infra tables are deliberately excluded — see the doc text). |
| `order-lifecycle.drawio` | `domain-model.md` | `Order` FSM state diagram. Blue = happy path, red = abnormal-exit terminals, amber = manager correction, green = success. |
| `item-lifecycle.drawio` | `domain-model.md` | `OrderItem` FSM state diagram, including the scrap → remake branch. |
| `simulator-actors.drawio` | `simulator.md` | Actor topology — who talks to the Forno API directly, who reports to the `ShadowLedger`. |
| `pillars.drawio` | `observability.md` | Metrics/logs/traces, correlation_id fan-out, through to Grafana/Alertmanager/Tempo. |
| `bootstrap-sequence.drawio` | `configuration.md` | `ManagerActor.bootstrap()`, built via `seqlayout.py`. |
| `ci-pipeline.drawio` | `CONTRIBUTING.md` | The six CI stages, each gating the next. |
| `testing-strategy.drawio` | `CONTRIBUTING.md` | The fast/integration/system testing pyramid. |

## Regenerating

Every diagram except `deployment-topology.drawio` was hand-built as a small graph-JSON file (nodes
+ edges, colored by [the shared tier palette](#color-palette)) and laid out with the skill's
`autolayout.py` (Graphviz), or — for the two sequence diagrams — `seqlayout.py`. None of that JSON
is checked in; if you need to edit a diagram's *content* rather than just its labels/layout,
either edit the `.drawio` XML directly in draw.io, or reconstruct the graph JSON from the node/edge
list visible in the current export and re-run `autolayout.py`.

`deployment-topology.drawio` is the one exception — it's fully reproducible from
`docker-compose.yml`:

```bash
python3 docs/media/generate-deployment-diagram.py --skill-dir <path to drawio-skill's skills/drawio-skill>
drawio -x -f png -e -o docs/media/deployment-topology.drawio.png docs/media/deployment-topology.drawio
```

For any `.drawio` file, re-export its PNG with:

```bash
xvfb-run -a drawio -x -f png -e -o <name>.drawio.png <name>.drawio   # headless; drop xvfb-run with a real display
python3 <drawio-skill>/scripts/repair_png.py <name>.drawio.png       # fixes a truncated-IEND-chunk issue in draw.io's PNG writer
```

Multi-page files (`architecture-c4.drawio`) need `--page-index N` (1-based) on the export command.

## Color palette

Every diagram reuses the same tier → color mapping, so the same color means the same thing
everywhere:

| Tier | Color | Used for |
|---|---|---|
| person | dark navy | Humans / simulated actors |
| edge | blue | `api`, the edge process |
| worker | cyan | `outbox-relay`, projectors, other background workers |
| state | amber | PostgreSQL, durable state, physical capacity (`IN_OVEN`) |
| messaging | purple | Redpanda, queues, brokers |
| observability | green | Prometheus, Grafana, Alertmanager, successful terminals |
| external | grey | `simulator`, external clients |
| domain | dark green | The pure domain layer |
| bad | red | Forbidden edges, failure/abnormal-exit states |
| decision | yellow | Decision points, choice pseudostates |

## Tooling

These were authored with [Agents365-ai/drawio-skill](https://github.com/Agents365-ai/drawio-skill),
which generates `.drawio` XML and exports it locally via the real draw.io desktop CLI — no browser
automation. Setting it up needs:

```bash
# draw.io desktop CLI (headless: needs xvfb, no GUI on this box)
sudo apt-get install -y xvfb libasound2t64 libgbm1
DRAWIO_URL=$(curl -s https://api.github.com/repos/jgraph/drawio-desktop/releases/latest \
  | grep -oE '"browser_download_url": *"[^"]*drawio-amd64-[0-9.]+\.deb"' | cut -d'"' -f4)
curl -L -o /tmp/drawio.deb "$DRAWIO_URL"
sudo dpkg -i /tmp/drawio.deb || sudo apt-get install -f -y

# Graphviz, for the skill's autolayout.py (Graphviz `dot` places nodes / routes edges)
sudo apt-get install -y graphviz
```

Every `drawio` invocation on a headless box needs the `xvfb-run -a` wrapper (it's an Electron app
with no real display to attach to) — e.g. `xvfb-run -a drawio -x -f png -e -o out.png in.drawio`.
