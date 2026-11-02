# ADR-0005: Global layers, not package-by-feature

**Status:** Accepted · **Date:** 2026-01-18

## Context

Two defensible structures: package-by-feature (`orders/`, `kitchen/`, `menu/`, each containing its
own domain, application and adapter code) or global layers (`domain/`, `application/`, `adapters/`).

Package-by-feature is the better answer at scale — it keeps change localised and makes extraction
into services mechanical. This system has about 24 endpoints and one aggregate root.

## Decision

**Global layers**, with dependencies enforced by import-linter in CI.

```
adapters → application → domain
```

`domain/` imports nothing outside the standard library. The build fails otherwise.

## Consequences

- A reviewer opens `domain/order.py` and sees **the entire business ruleset in one file**. With
  package-by-feature at this size, the same ruleset is split across three directories connected by
  nothing but convention.
- The layer rule is machine-checked, which is what separates this from the many repositories that
  claim clean architecture in a README and import `HTTPException` in a service.
- Cost: at scale, layers spread a single feature change across three directories. That cost is real
  and grows.

## Alternatives rejected

**Package-by-feature.** Correct above roughly 50 endpoints or 3+ aggregate roots, and the right
choice when a repository is heading for extraction into services.

**Flat modules.** Fine for 500 lines. The layering rule is the thing worth demonstrating, and it
needs somewhere to live.

## What would change this

Roughly 50 endpoints, or a second aggregate root with its own lifecycle (a delivery or inventory
domain would qualify). The migration is mechanical because the layer boundaries are already explicit
and enforced — the contracts move from `layers` to `independence` in one config file.
