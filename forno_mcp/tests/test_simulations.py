"""Simulation jobs, with a stand-in for the simulator process so the tests
don't need a running stack."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from forno_mcp.clients import FornoApi
from forno_mcp.simulations import SimulationRunner

from .fakes import order

pytestmark = pytest.mark.asyncio

FAKE_SIM = """
import os, sys, time
print("scenario file:", os.environ["FORNO_SIM_SCENARIO"])
print("\\x1b[32morder placed\\x1b[0m table=4")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
print("─── forno simulation report ───")
print(" orders    10 placed · 9 served")
print(" invariants  4/4 ✓")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


@pytest.fixture
def runner(settings, forno, tmp_path: Path):
    script = tmp_path / "fake_sim.py"
    script.write_text(FAKE_SIM)
    cfg = replace(settings, simulator_cmd=[sys.executable, str(script)])
    return SimulationRunner(cfg, FornoApi(cfg, transport=forno.transport()))


async def wait_done(runner, job_id):
    for _ in range(100):
        job = runner.get(job_id)
        if job.state != "running":
            return job
        await asyncio.sleep(0.05)
    msg = "simulation did not finish"
    raise AssertionError(msg)


async def test_job_runs_and_captures_the_report(runner):
    job = await runner.start("busy-friday", duration_minutes=10)
    done = await wait_done(runner, job.id)
    out = done.public()
    assert out["state"] == "succeeded"
    assert out["invariants"] == "invariants  4/4 ✓"
    assert "order placed table=4" in out["recent_log"]  # ANSI colour stripped


async def test_retimes_scenario_to_the_live_api(runner, forno):
    forno.time_scale = 1
    job = await runner.start("busy-friday", duration_minutes=2)
    done = await wait_done(runner, job.id)
    assert done.time_scale == 1
    assert any("re-timed to 1x" in n for n in done.notes)
    copy = Path(done.lines[0].split(": ", 1)[1])
    assert yaml.safe_load(copy.read_text())["simulation"]["time_scale"] == 1  # noqa: ASYNC240


async def test_refuses_mismatch_when_not_allowed_to_retime(runner):
    with pytest.raises(RuntimeError, match="TIME_SCALE=120"):
        await runner.start("busy-friday", match_api_time_scale=False)


async def test_one_simulation_at_a_time(runner, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "1")
    first = await runner.start("busy-friday", duration_minutes=1)
    with pytest.raises(RuntimeError, match="still running"):
        await runner.start("oven-down", duration_minutes=1)
    stopped = await runner.stop(first.id)
    assert stopped.stop_requested
    assert (await wait_done(runner, first.id)).state == "stopped"


async def test_warns_about_leftover_orders(runner, forno):
    forno.orders = [order("old", "IN_KITCHEN")]
    job = await runner.start("busy-friday", duration_minutes=1)
    assert any("already IN_KITCHEN" in n for n in job.notes)
    await wait_done(runner, job.id)


async def test_failed_run_is_reported(runner, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "1")
    job = await runner.start("busy-friday", duration_minutes=1)
    assert (await wait_done(runner, job.id)).state == "failed"


async def test_unknown_scenario_lists_the_real_ones(runner):
    with pytest.raises(ValueError, match="oven-down"):
        await runner.start("does-not-exist")
