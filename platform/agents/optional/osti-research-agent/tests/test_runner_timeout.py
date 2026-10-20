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

from research_agent.core import runner as runner_module
from research_agent.core.config import AgentConfig
from research_agent.core.events import FinalResult
from research_agent.core.runner import ResearchRunner


async def _fake_complete(prompt, *, max_tokens, temperature=0):
    return "stub response"


async def _hanging_answer_question(
    question,
    complete,
    emit,
    redis_client,
    pdf_cache=None,
    text_cache=None,
    findings=None,
    config=None,
    caches=None,
):
    """Stub pipeline: records one finding, then hangs past the test's budget."""
    if findings is not None:
        findings.append(
            {
                "osti_id": "123",
                "title": "Partial Paper",
                "url": "https://osti.gov/biblio/123",
                "excerpt": "a partial excerpt",
            }
        )
    await asyncio.sleep(3600)


async def _drain(runner, question, run_id):
    events = []
    async for event in runner.run(question, run_id=run_id):
        events.append(event)
    return events


def test_budget_timeout_yields_timeout_partial_with_findings(monkeypatch):
    monkeypatch.setattr(
        runner_module.pipeline, "answer_question", _hanging_answer_question
    )

    # total_budget_s - 30 == 1s: enough wall-clock for the stub to run its
    # synchronous prefix (append the finding) before its sleep is cancelled.
    config = AgentConfig(
        litellm_base_url="http://unused", total_budget_s=31, engine="pipeline"
    )
    runner = ResearchRunner(config, complete=_fake_complete)

    events = asyncio.run(_drain(runner, "does titanium creep at 900C?", "t3"))

    assert events
    final = events[-1]
    assert isinstance(final, FinalResult)
    assert final.status == "timeout_partial"
    assert final.papers_analyzed == 1
    assert final.provenance and final.provenance[0]["source"] == "OSTI #123"
    assert "confidence" not in final.provenance[0]
