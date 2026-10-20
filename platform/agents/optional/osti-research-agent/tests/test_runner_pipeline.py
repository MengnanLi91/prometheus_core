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

import asyncio

from research_agent import osti_client
from research_agent.core.config import AgentConfig
from research_agent.core.events import FinalResult, ProgressEvent
from research_agent.core.runner import ResearchRunner


async def _fake_complete(prompt, *, max_tokens, temperature=0):
    return "stub response"


def _config(**overrides):
    # These tests exercise the legacy pipeline rather than the default engine.
    overrides.setdefault("engine", "pipeline")
    return AgentConfig(litellm_base_url="http://unused", total_budget_s=60, **overrides)


async def _drain(runner, question, run_id):
    events = []
    async for event in runner.run(question, run_id=run_id):
        events.append(event)
    return events


def test_event_stream_is_progress_then_exactly_one_final(monkeypatch):
    # No OSTI results -> the pipeline's fast "no_results" path, no real network/LLM calls.
    monkeypatch.setattr(osti_client, "search_osti", lambda *a, **k: [])

    runner = ResearchRunner(_config(), complete=_fake_complete)
    events = asyncio.run(_drain(runner, "does titanium creep at 900C?", "t1"))

    assert events, "expected at least the final event"
    assert all(isinstance(e, ProgressEvent) for e in events[:-1])
    assert isinstance(events[-1], FinalResult)
    assert events[-1].status == "completed"
    assert sum(isinstance(e, FinalResult) for e in events) == 1


def test_exception_yields_error_status_not_a_hang(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("network is down")

    monkeypatch.setattr(osti_client, "search_osti", _boom)

    runner = ResearchRunner(_config(), complete=_fake_complete)
    events = asyncio.run(_drain(runner, "does titanium creep at 900C?", "t2"))

    assert events
    assert isinstance(events[-1], FinalResult)
    assert events[-1].status == "error"
    assert sum(isinstance(e, FinalResult) for e in events) == 1
