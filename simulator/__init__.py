"""Forno's external-client simulator.

No imports from `forno`, no database access, no shared process — enforced by
import-linter (`simulator-is-external`). See docs/adr/0008-simulator-as-external-client.md
and docs/simulator.md.

These are discrete-event-simulation *actors*, not LLM agents — see
docs/simulator.md "Naming".
"""
