# This file is part of Prometheus
# https://inl.gov/super-intelligence/prometheus/
#
# Copyright 2026 Battelle Energy Alliance, LLC
# All rights reserved, see NOTICE.txt for full restrictions
# https://github.com/idaholab/prometheus_core/blob/main/NOTICE.txt
#
# Licensed under the MIT License, please see LICENSE for details
# https://github.com/idaholab/prometheus_core/blob/main/LICENSE
#
# SPDX-License-Identifier: MIT

"""Progress display for an evaluation run, on stderr.

On a terminal it is one line redrawn in place, refreshed every second so the
clock keeps moving through long parses:

    [######------------] 3/10  30%  deepagent  uq-fp-...  12m04s  ETA 28m10s  Parsing paper...

Off a terminal (CI, a log file) it prints one line per question start and
finish instead, so logs stay readable. Log records and per-question results
are written above the bar without breaking it.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import time
from typing import Any, Dict, Optional, TextIO

BAR_WIDTH = 18
REFRESH_S = 1.0


def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m{secs:02d}s" if minutes else f"{secs}s"


class Progress:
    def __init__(self, total: int, stream: Optional[TextIO] = None, live: Optional[bool] = None):
        self.total = max(total, 0)
        self.stream = stream or sys.stderr
        self.live = self.stream.isatty() if live is None else live
        self.done = 0
        self.engine = ""
        self.qid = ""
        self.status = ""
        self.started = time.monotonic()
        self.question_started = self.started
        self._finished_times: list[float] = []
        self._ticker: Optional[asyncio.Task] = None
        self._drawn = False

    # --- lifecycle --------------------------------------------------------

    async def __aenter__(self) -> "Progress":
        if self.live:
            self._ticker = asyncio.create_task(self._tick())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._ticker is not None:
            self._ticker.cancel()
            try:
                await self._ticker
            except asyncio.CancelledError:
                pass
        self._clear()

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(REFRESH_S)
            self.draw()

    # --- events -----------------------------------------------------------

    def begin(self, engine: str, qid: str, question: str) -> None:
        self.engine, self.qid, self.status = engine, qid, "starting"
        self.question_started = time.monotonic()
        if self.live:
            self.draw()
        else:
            self.write(f"[{engine} {self.done + 1}/{self.total}] {qid}: {question}")

    def update(self, message: str) -> None:
        self.status = " ".join(str(message).split())
        if self.live:
            self.draw()

    def finish(self, record: Dict[str, Any], summary: str) -> None:
        self._finished_times.append(time.monotonic() - self.question_started)
        self.done += 1
        self.status = ""
        self.write(f"  {'ok ' if not summary.startswith('FAIL') else ''}{summary}")

    # --- output -----------------------------------------------------------

    def write(self, text: str) -> None:
        """Print a line above the bar (or plainly, off a terminal)."""
        self._clear()
        self.stream.write(text.rstrip("\n") + "\n")
        self.stream.flush()
        if self.live and self.done < self.total:
            self.draw()

    def eta(self) -> Optional[float]:
        if not self._finished_times:
            return None
        remaining = self.total - self.done
        average = sum(self._finished_times) / len(self._finished_times)
        current = time.monotonic() - self.question_started
        return max(average * remaining - (current if remaining else 0), 0)

    def line(self, width: int) -> str:
        fraction = self.done / self.total if self.total else 1.0
        filled = int(round(BAR_WIDTH * fraction))
        bar = "#" * filled + "-" * (BAR_WIDTH - filled)
        eta = self.eta()
        parts = [
            f"[{bar}] {self.done}/{self.total} {int(fraction * 100):3d}%",
            self.engine,
            self.qid,
            _clock(time.monotonic() - self.started),
            f"ETA {_clock(eta)}" if eta is not None else "ETA --",
            f"q {_clock(time.monotonic() - self.question_started)}",
            self.status,
        ]
        text = "  ".join(p for p in parts if p)
        return text if len(text) <= width else text[: max(width - 1, 0)] + "…"

    def draw(self) -> None:
        if not self.live:
            return
        width = shutil.get_terminal_size((100, 20)).columns - 1
        self.stream.write("\r\x1b[2K" + self.line(width))
        self.stream.flush()
        self._drawn = True

    def _clear(self) -> None:
        if self.live and self._drawn:
            self.stream.write("\r\x1b[2K")
            self.stream.flush()
            self._drawn = False


class ProgressLogHandler(logging.Handler):
    """Route log records through ``Progress.write`` so they don't break the bar."""

    def __init__(self, progress: Progress, level: int = logging.WARNING):
        super().__init__(level)
        self.progress = progress
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.progress.write(self.format(record))
        except Exception:  # logging must never raise
            self.handleError(record)
