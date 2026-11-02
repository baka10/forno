"""Prints the simulation report — see docs/simulator.md "The shadow ledger"
for the format. Exit code is non-zero if any invariant fails, which is what
lets `make demo` run as a CI gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulator.shadow_ledger import InvariantResult, ShadowLedger

_RULE_WIDTH = 63


def print_report(  # noqa: PLR0913 -- one param per report field
    scenario_name: str,
    seed: int,
    duration_minutes: int,
    time_scale: int,
    ledger: ShadowLedger,
    results: list[InvariantResult],
) -> int:
    """Returns the process exit code."""
    passed = sum(1 for result in results if result.passed)
    total = len(results)

    lines = [
        f"─── forno simulation report {'─' * (_RULE_WIDTH - 29)}",
        (
            f" scenario  {scenario_name}    seed {seed}    "
            f"duration {duration_minutes}m @ {time_scale}x"
        ),
        (
            f" orders    {ledger.orders_placed} placed · {ledger.orders_served} served · "
            f"{ledger.orders_cancelled} cancelled · {ledger.orders_voided} voided · "
            f"{ledger.walkouts} walkout"
        ),
        (
            f" items     {len(ledger.delivered_item_ids)} delivered "
            f"({ledger.items_ordered} ordered + {ledger.remakes} remakes "
            f"− {ledger.scrapped} scrapped)"  # noqa: RUF001 -- deliberate minus sign, matches the report's box-drawing style
        ),
    ]
    if ledger.promise_deltas_seconds:
        p50 = _percentile(ledger.promise_deltas_seconds, 50)
        p95 = _percentile(ledger.promise_deltas_seconds, 95)
        breaches = sum(1 for delta in ledger.promise_deltas_seconds if delta > 0)
        breach_rate = breaches / len(ledger.promise_deltas_seconds)
        lines.append(
            f" promise   p50 {p50:+.0f}s · p95 {p95:+.0f}s (domain s vs promised_at) "
            f"· breach {breach_rate:.1%}"
        )

    status = "✓" if passed == total else "✗"
    lines.append(f" invariants  {passed}/{total} {status}")
    lines.extend(
        f"   ✗ #{result.number} {result.description}: {result.detail}"
        for result in results
        if not result.passed
    )
    lines.append("─" * _RULE_WIDTH)

    print("\n".join(lines))  # noqa: T201 - this IS the product; make demo's whole output is this report
    return 0 if passed == total else 1


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(int(len(ordered) * pct / 100), len(ordered) - 1)
    return ordered[index]
