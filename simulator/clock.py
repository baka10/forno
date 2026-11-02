"""The simulator's own `Clock`-shaped time source. Deliberately not shared
with `forno.domain.clock` — the simulator may not import `forno`
(import-linter: `simulator-is-external`). See docs/adr/0009-scaled-domain-time.md.

Domain durations (bake, prep, deliberation, walkout thresholds) are divided
by `time_scale`; wall clock is untouched. Scraping intervals, `rate()`, and
burn-rate windows must stay real, or the observability stack becomes fiction.
"""

from __future__ import annotations

import asyncio


class ScaledClock:
    def __init__(self, time_scale: int) -> None:
        self.time_scale = time_scale

    async def sleep_domain_seconds(self, seconds: float) -> None:
        """Sleeps `seconds / time_scale` of wall-clock time."""
        await asyncio.sleep(seconds / self.time_scale)
