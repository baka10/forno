"""Use cases — one transaction each. See docs/architecture.md "Layering".

Depends on `domain/` and the `adapters/db` unit-of-work port; never imports
`fastapi`, `aiokafka`, or anything from `adapters/http` or `adapters/messaging`
directly (import-linter: `layers`).
"""
