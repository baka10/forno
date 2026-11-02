from __future__ import annotations

from typing import TYPE_CHECKING

from simulator.report import print_report
from simulator.shadow_ledger import InvariantResult, ShadowLedger

if TYPE_CHECKING:
    import pytest


def test_print_report_returns_zero_when_all_invariants_pass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ledger = ShadowLedger()
    results = [InvariantResult(number=1, description="x", passed=True)]

    code = print_report("tiny", 42, 5, 60, ledger, results)

    assert code == 0
    out = capsys.readouterr().out
    assert "tiny" in out
    assert "1/1" in out


def test_print_report_returns_one_when_any_invariant_fails(
    capsys: pytest.CaptureFixture[str],
) -> None:
    ledger = ShadowLedger()
    results = [
        InvariantResult(number=1, description="x", passed=True),
        InvariantResult(number=2, description="y", passed=False, detail="broke"),
    ]

    code = print_report("tiny", 42, 5, 60, ledger, results)

    assert code == 1
    out = capsys.readouterr().out
    assert "1/2" in out
    # The failing invariant is named with its detail — a red CI run must say why.
    assert "#2 y: broke" in out
    assert "#1 x" not in out
