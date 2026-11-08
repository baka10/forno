"""Run the simulator against the *live* stack, as a background job.

The simulator already is the system's best experiment: it bootstraps the
pizzeria through the management API, drives every role through the public
contract and ends with an invariant report (docs/simulator.md). This module
lets an agent start one, watch it, and read the report — without blocking a
tool call for the minutes a service takes.

Two live-system details it handles so the agent doesn't have to:

* Time scale (ADR-0009). The API computes promises at its own
  FORNO_TIME_SCALE; the simulator refuses to run a scenario whose time_scale
  differs. By default the job runs a copy of the scenario re-timed to the
  API's scale, and says so.
* Leftovers. Orders still IN_KITCHEN from an earlier run hold oven slots
  (Makefile "demo"); the job warns before starting into a dirty kitchen.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import tempfile
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from forno_mcp.knowledge import load_scenario

if TYPE_CHECKING:
    from forno_mcp.clients import FornoApi
    from forno_mcp.config import Settings

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_REPORT_START = "─── forno simulation report"
_TAIL_LINES = 400
_RECENT = 15
_SIM_REFUSED = 2  # simulator/__main__.py: exit code when it refuses to start
# The simulator logs one line per event; a traceback or a long report line can
# exceed asyncio's default 64 KiB line limit, which would kill the pump.
_LINE_LIMIT = 1024 * 1024


@dataclass
class SimulationJob:
    id: str
    scenario: str
    seed: int
    duration_minutes: int
    time_scale: int
    expected_wall_seconds: float | None
    notes: list[str]
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    state: str = "running"  # running | succeeded | failed | stopped
    exit_code: int | None = None
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=_TAIL_LINES))
    report: list[str] = field(default_factory=list)
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    stop_requested: bool = False

    def public(self, *, full_log: bool = False) -> dict[str, Any]:
        elapsed = (self.finished_at or time.time()) - self.started_at
        progress = None
        if self.expected_wall_seconds:
            progress = min(1.0, round(elapsed / self.expected_wall_seconds, 2))
        out: dict[str, Any] = {
            "job_id": self.id,
            "scenario": self.scenario,
            "seed": self.seed,
            "duration_minutes": self.duration_minutes or "open-ended",
            "time_scale": self.time_scale,
            "state": self.state,
            "exit_code": self.exit_code,
            "elapsed_wall_seconds": round(elapsed, 1),
            "expected_wall_seconds": self.expected_wall_seconds,
            "progress": progress,
            "notes": self.notes,
            "recent_log": list(self.lines)[-_RECENT:] if not full_log else list(self.lines),
        }
        if self.report:
            out["report"] = "\n".join(self.report)
            out["invariants"] = next(
                (ln.strip() for ln in self.report if ln.strip().startswith("invariants")), None
            )
        return out


class SimulationRunner:
    def __init__(self, settings: Settings, api: FornoApi) -> None:
        self._settings = settings
        self._api = api
        self._jobs: dict[str, SimulationJob] = {}
        self._tmp = Path(tempfile.mkdtemp(prefix="forno-mcp-"))
        # Held from the "is one running?" check until the job is registered:
        # the SDK serves tool calls concurrently, and two simulators on one
        # pizzeria corrupt each other's shadow ledgers.
        self._start_lock = asyncio.Lock()
        self._pumps: set[asyncio.Task[None]] = set()

    def get(self, job_id: str) -> SimulationJob:
        if job_id not in self._jobs:
            msg = f"Unknown job_id '{job_id}'. list_simulations shows the jobs of this session."
            raise ValueError(msg)
        return self._jobs[job_id]

    def list(self) -> list[SimulationJob]:
        return sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)

    def running(self) -> SimulationJob | None:
        return next((j for j in self._jobs.values() if j.state == "running"), None)

    async def _api_time_scale(self) -> int:
        resp = await self._api.request("GET", "/health/ready")
        if resp.status_code != 200:  # noqa: PLR2004
            msg = f"API is not ready ({resp.status_code}); start the stack with `make up` first."
            raise RuntimeError(msg)
        return int(resp.json().get("time_scale", 1))

    async def _leftovers(self) -> int:
        page = await self._api.get_json("/orders", params={"status": "IN_KITCHEN", "limit": 200})
        return len(page)

    def _retimed_copy(self, path: Path, time_scale: int) -> Path:
        """Write the re-timed scenario as `<dir>/<name>.yaml`: the simulator
        takes a directory and a scenario name, never a file path."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        raw.setdefault("simulation", {})["time_scale"] = time_scale
        target_dir = self._tmp / f"{time_scale}x"
        target_dir.mkdir(exist_ok=True)
        copy = target_dir / path.name
        copy.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return copy

    async def start(
        self,
        scenario: str,
        *,
        seed: int = 42,
        duration_minutes: int | None = None,
        match_api_time_scale: bool = True,
    ) -> SimulationJob:
        async with self._start_lock:
            return await self._start_locked(
                scenario,
                seed=seed,
                duration_minutes=duration_minutes,
                match_api_time_scale=match_api_time_scale,
            )

    async def _start_locked(
        self,
        scenario: str,
        *,
        seed: int,
        duration_minutes: int | None,
        match_api_time_scale: bool,
    ) -> SimulationJob:
        if (active := self.running()) is not None:
            msg = (
                f"Simulation {active.id} ({active.scenario}) is still running. Two simulators "
                "would share one pizzeria and corrupt each other's invariants; stop it first."
            )
            raise RuntimeError(msg)
        path = self._settings.scenarios_dir / f"{scenario}.yaml"
        if not path.exists():
            names = sorted(p.stem for p in self._settings.scenarios_dir.glob("*.yaml"))
            msg = f"Unknown scenario '{scenario}'. Available: {', '.join(names)}."
            raise ValueError(msg)
        info = load_scenario(path)
        api_scale = await self._api_time_scale()
        notes: list[str] = []
        run_path, scale = path, info.time_scale
        if api_scale != info.time_scale:
            if not match_api_time_scale:
                msg = (
                    f"Scenario '{scenario}' declares time_scale {info.time_scale} but the live API "
                    f"runs at {api_scale}. Restart the stack with TIME_SCALE={info.time_scale}, "
                    "or allow re-timing the scenario to the API's scale."
                )
                raise RuntimeError(msg)
            run_path, scale = self._retimed_copy(path, api_scale), api_scale
            notes.append(
                f"Scenario declares {info.time_scale}x, the live API runs at {api_scale}x: running "
                f"a copy re-timed to {api_scale}x so promises stay in the right unit (ADR-0009)."
            )
        duration = duration_minutes if duration_minutes is not None else info.duration_minutes
        expected = duration * 60 / scale if duration else None
        if expected and expected > 15 * 60:
            notes.append(
                f"At {scale}x this run takes about {expected / 60:.0f} wall-clock minutes; "
                "pass duration_minutes to shorten it."
            )
        leftovers = await self._leftovers()
        if leftovers:
            notes.append(
                f"{leftovers} order(s) are already IN_KITCHEN from earlier traffic and hold oven "
                "slots; expect worse promises than the scenario alone would produce."
            )

        job = SimulationJob(
            id=uuid.uuid4().hex[:8],
            scenario=info.name,
            seed=seed,
            duration_minutes=duration,
            time_scale=scale,
            expected_wall_seconds=round(expected, 1) if expected else None,
            notes=notes,
        )
        env = {
            **os.environ,
            "FORNO_SIM_API_URL": self._settings.api_url,
            "FORNO_SIM_SCENARIO": run_path.stem,
            "FORNO_SIM_SCENARIO_DIR": str(run_path.parent),
            "FORNO_SIM_SEED": str(seed),
            "FORNO_SIM_DURATION_MINUTES": str(duration),
        }
        job.process = await asyncio.create_subprocess_exec(
            *self._settings.simulator_cmd,
            cwd=self._settings.repo_root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=_LINE_LIMIT,
        )
        self._jobs[job.id] = job
        pump = asyncio.create_task(self._pump(job))
        self._pumps.add(pump)  # the loop keeps only a weak reference to tasks
        pump.add_done_callback(self._pumps.discard)
        return job

    async def _pump(self, job: SimulationJob) -> None:
        """Collect output until the process exits, and always settle the job:
        a job stuck in `running` would refuse every later start."""
        process = job.process
        assert process is not None  # noqa: S101 -- set by start() before scheduling
        assert process.stdout is not None  # noqa: S101
        try:
            await self._read_output(job, process.stdout)
        except Exception as exc:  # noqa: BLE001 -- recorded on the job; the process is stopped below
            job.notes.append(f"Lost the simulator's output ({exc!r}); stopping it.")
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
        finally:
            job.exit_code = await process.wait()
            job.finished_at = time.time()
            if job.stop_requested:
                job.state = "stopped"
            else:
                job.state = "succeeded" if job.exit_code == 0 else "failed"
            if job.exit_code == _SIM_REFUSED and not job.report:
                job.notes.append("Simulator refused to start; see recent_log for the reason.")

    @staticmethod
    async def _read_output(job: SimulationJob, stdout: asyncio.StreamReader) -> None:
        in_report = False
        async for raw in stdout:
            line = _ANSI.sub("", raw.decode("utf-8", "replace")).rstrip()
            if not line:
                continue
            job.lines.append(line)
            if line.startswith(_REPORT_START):
                in_report = True
            if in_report:
                job.report.append(line)

    async def stop(self, job_id: str) -> SimulationJob:
        job = self.get(job_id)
        if job.state == "running" and job.process is not None:
            job.stop_requested = True
            with contextlib.suppress(ProcessLookupError):
                job.process.terminate()  # simulator stops early on SIGTERM, then reports
        return job
