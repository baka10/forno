.PHONY: up down clear demo live seed-prompt seed chaos test test-integration test-mcp mcp mcp-http logs lint fmt migrate build

SCENARIO ?= busy-friday
SEED ?= 42
DURATION ?=
TARGET ?= kitchen
MODE ?=
SERVICE ?= api
## The API must run at the scenario's time_scale (ADR-0009) or every promise
## is computed in the wrong unit — read it from the scenario file.
TIME_SCALE ?= $(shell awk '/^[[:space:]]*time_scale:/ {print $$2; exit}' scenarios/$(SCENARIO).yaml)

## Core + observability stack, healthy in <60s. Empty pizzeria on first run —
## offers to populate it (see seed-prompt) unless run non-interactively.
## Gated on the fast test suite — a broken lint/type/test state should
## never reach running containers.
up: test
	docker compose --profile core --profile observability up --build -d
	@echo "api:        http://localhost:8000/docs"
	@echo "grafana:    http://localhost:3000 (admin/admin)"
	@echo "prometheus: http://localhost:9090"
	@echo "redpanda console: http://localhost:8080"
	@echo "pgadmin:    http://localhost:5050 (admin@forno.dev/admin) — forno server pre-connected"
	@$(MAKE) --no-print-directory seed-prompt

## Asks whether to populate the pizzeria from scenarios/$(SCENARIO).yaml, but
## only if the menu is still empty and stdin is a real terminal (never
## prompts in CI or when piped) — safe to run after every `make up`.
seed-prompt:
	@if [ ! -t 0 ]; then exit 0; fi; \
	if ! timeout 30 sh -c 'until curl -sf http://localhost:8000/health/ready >/dev/null 2>&1; do sleep 1; done'; then \
	  exit 0; \
	fi; \
	menu=$$(curl -sf http://localhost:8000/menu 2>/dev/null || echo '[]'); \
	if [ "$$menu" != "[]" ]; then exit 0; fi; \
	printf 'No menu yet. Populate the example pizzeria from scenarios/%s.yaml? [y/N] ' "$(SCENARIO)"; \
	read -r ans; \
	case "$$ans" in \
	  y|Y) $(MAKE) --no-print-directory seed ;; \
	  *) echo "Skipped — run 'make seed' any time." ;; \
	esac

## Populates menu/ovens/stations/tables/shifts from scenarios/$(SCENARIO).yaml
## through the real management API — ManagerActor.bootstrap(), the same
## mechanism the simulator itself uses, run once and exited. No traffic
## started; pair with `make demo`/`make live` for that.
## `--no-deps`: the stack is already up; without it compose would recreate
## `api` with this shell's (unset) TIME_SCALE and silently change its clock.
seed:
	docker compose --profile core --profile sim build simulator
	docker compose --profile core --profile sim run --rm --no-deps \
	  -e FORNO_SIM_SCENARIO=$(SCENARIO) -e FORNO_SIM_BOOTSTRAP_ONLY=true simulator

down:
	docker compose --profile core --profile observability --profile sim down -v

## Remove everything this project owns: containers, volumes, networks, and
## every locally-built image (api/migrate/relay/projectors/simulator).
## Pulled third-party images (postgres, redpanda, prometheus, grafana,
## alertmanager, redpanda-console) are left alone — `--rmi local` only
## touches images with no explicit `image:` tag in docker-compose.yml.
clear:
	docker compose --profile core --profile observability --profile sim \
	  down --rmi local --volumes --remove-orphans

## Run a full simulated service and print the invariant report. Exit code is
## the simulator's — non-zero if any scenario invariant fails.
##
## Tears down *with volumes*: a run must start from an empty pizzeria. Orders
## left IN_KITCHEN by a previous run would keep their oven slots forever and
## starve the next run's chefs (every oven-in 422s, everyone walks out).
##
## The simulator is run with `--no-deps`: `api` is already up (and healthy)
## at the scenario's TIME_SCALE; letting compose "ensure" the dependency
## would recreate it with a different config hash and reset its clock.
##
## Not `up --abort-on-container-exit --exit-code-from simulator`: `migrate` is
## a one-shot in the core profile that exits 0 as soon as it finishes, and
## --abort-on-container-exit tears the whole stack down on *any* container
## exit — so it aborted (killing a still-booting api with SIGKILL/137) before
## the simulator ever started. Bring core up first, wait for readiness, then
## run the simulator as the one foreground container whose exit code we want.
demo:
	docker compose --profile core --profile sim down -v
	# `up --build` only rebuilds the services it starts; the simulator is run
	# separately below, so build it here or edits to simulator/ never ship.
	docker compose --profile core --profile sim build simulator
	TIME_SCALE=$(TIME_SCALE) docker compose --profile core up --build -d
	@timeout 90 sh -c 'until curl -sf http://localhost:8000/health/ready >/dev/null 2>&1; do sleep 1; done' \
	  || { echo "api did not become ready in time"; docker compose --profile core --profile sim down -v; exit 1; }
	SCENARIO=$(SCENARIO) SEED=$(SEED) DURATION=$(DURATION) TIME_SCALE=$(TIME_SCALE) \
	  docker compose --profile core --profile sim run --rm --no-deps simulator; \
	  code=$$?; \
	  docker compose --profile core --profile sim down -v; \
	  exit $$code

## Real-time, open-ended pizzeria — for watching Grafana, not CI. Ctrl-C to stop.
live:
	@echo "api:        http://localhost:8000/docs"
	@echo "grafana:    http://localhost:3000 (admin/admin)"
	@echo "prometheus: http://localhost:9090"
	SCENARIO=live-demo DURATION=0 TIME_SCALE=1 \
	  docker compose --profile core --profile sim --profile observability up --build

## Kill a worker mid-service, then prove recovery. See docs/runbook.md.
chaos:
	@if [ "$(MODE)" = "redeliver" ]; then \
	  echo "chaos: forcing duplicate delivery is not yet implemented — see docs/runbook.md 'Deliberate chaos'"; \
	elif [ "$(MODE)" = "slow-client" ]; then \
	  echo "chaos: SSE subscriber overflow is not yet implemented — see docs/runbook.md 'Deliberate chaos'"; \
	else \
	  svc=$$([ "$(TARGET)" = relay ] && echo outbox-relay || echo projector-$(TARGET)); \
	  docker compose kill -s SIGKILL $$svc; \
	  docker compose up -d $$svc; \
	fi

## Fast gate: lint, mypy, import-linter, pure unit tests. <30s, no containers.
test:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy --strict src/forno/domain src/forno/application
	uv run mypy src/forno/adapters simulator tests
	uv run lint-imports --config importlinter.ini
	uv run pytest tests/domain tests/application tests/adapters tests/contracts -q

## Testcontainers: Postgres. A few crucial boundaries (oven capacity,
## optimistic locking, outbox SKIP LOCKED, inbox dedup) — not exhaustive.
test-integration:
	uv run pytest tests/integration tests/contract -q -m "integration or contract"

## Testcontainers: Postgres + Kafka, real HTTP client, the real app.
## Narrow on purpose — order placement + visibility. See tests/e2e/.
test-e2e:
	uv run pytest tests/e2e -q -m e2e

## MCP server for coding agents (docs/mcp.md). stdio is what Claude Code,
## Copilot and Claude Desktop spawn themselves; run it by hand only to check
## it starts. mcp-http serves Streamable HTTP for clients that want a URL.
mcp:
	uv run --project forno_mcp --quiet python -m forno_mcp

mcp-http:
	uv run --project forno_mcp --quiet python -m forno_mcp --http

## The MCP server's own gate: strict mypy + tests against a fake API and a
## real in-process MCP client. No containers. (ruff runs in `make test`.)
test-mcp:
	cd forno_mcp && uv run mypy .
	cd forno_mcp && uv run pytest -q

logs:
	docker compose logs -f --tail=200 $(SERVICE)

lint:
	uv run ruff check --fix .
	uv run ruff format .

## Apply pending Alembic migrations against the running Postgres container.
migrate:
	docker compose run --rm migrate

build:
	docker compose build
