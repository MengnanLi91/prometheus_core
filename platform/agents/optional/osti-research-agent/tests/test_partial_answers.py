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

"""Why papers were not used, and "nearest" findings: data the paper reports at
other conditions than those asked, kept and labelled instead of discarded."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from research_agent import llm_steps
from research_agent.core import deep_agent, deep_runner, prompts
from research_agent.core.config import AgentConfig
from research_agent.core.events import Finding
from research_agent.tools import progress
from tests.test_deep_agent import (
    _FakeRunnable,
    _analysis_result,
    _verification_context,
    _verification_result,
)

QUESTION = "What is the creep rate of SS316 at 400 C and 15 MPa?"


@pytest.fixture
def emitted(monkeypatch):
    messages = []

    def emit(message, detail=None):
        messages.append(message)

    monkeypatch.setattr(progress, "emit", emit)
    monkeypatch.setattr(deep_agent.progress, "emit", emit)
    return messages


def _analyze(ctx, analysis, verification=None):
    return asyncio.run(
        deep_agent._analyze_paper(
            "123",
            QUESTION,
            analyzer=_FakeRunnable(result=analysis),
            verifier=_FakeRunnable(result=verification or _verification_result()),
            ctx=ctx,
        )
    )


def _declined(reason):
    return {"messages": [AIMessage(content=json.dumps({"answered": False, "reason": reason}))]}


# --- 1: why a paper was not used -----------------------------------------


def test_analyzer_decline_is_shown_and_kept(tmp_path, emitted):
    ctx = _verification_context(tmp_path)

    _analyze(ctx, _declined("Reports creep only at 550-700 C."))

    assert "Paper 123 not used (analyzer): Reports creep only at 550-700 C." in emitted
    assert ctx.unused_papers["123"] == {
        "by": "analyzer",
        "reason": "Reports creep only at 550-700 C.",
    }


def test_verifier_rejection_is_shown_with_its_reason(tmp_path, emitted):
    ctx = _verification_context(tmp_path)
    rejected = _verification_result(
        verdict="rejected", answer="", evidence="", excerpt="", artifacts=[],
        reason="Table 1 has no 400 C row.",
    )

    _analyze(ctx, _analysis_result(), rejected)

    assert "Paper 123 not used (verifier): Table 1 has no 400 C row." in emitted
    assert ctx.unused_papers["123"]["by"] == "verifier"


# --- 3: nearest findings --------------------------------------------------


def _nearest_analysis():
    finding = {
        "source_id": "123", "match": "nearest",
        "answer": "Reported at 425 C and 20 MPa, not 400 C and 15 MPa: 1e-8 /s.",
        "evidence": "Table 1, 425 C row.", "excerpt": "creep begins above 800 C",
        "artifacts": ["/papers/123/paper.md"],
    }
    return {"messages": [AIMessage(content=json.dumps(finding))]}


def test_nearest_finding_is_verified_and_keeps_its_label(tmp_path):
    ctx = _verification_context(tmp_path)

    result = _analyze(ctx, _nearest_analysis(), _verification_result(match="nearest"))

    assert result["match"] == "nearest"
    assert ctx.findings[0].match == "nearest"


def test_verifier_can_downgrade_exact_to_nearest(tmp_path):
    ctx = _verification_context(tmp_path)

    _analyze(ctx, _analysis_result(), _verification_result(verdict="corrected", match="nearest"))

    assert ctx.findings[0].match == "nearest"


def test_missing_verifier_match_keeps_the_candidate_label(tmp_path):
    ctx = _verification_context(tmp_path)

    _analyze(ctx, _nearest_analysis(), _verification_result())

    assert ctx.findings[0].match == "nearest"


def test_unknown_match_is_a_protocol_failure(tmp_path):
    ctx = _verification_context(tmp_path)

    result = _analyze(ctx, _analysis_result(), _verification_result(match="close-ish"))

    assert result["answered"] is False
    assert ctx.verification_errors == ["123: verifier_protocol_failure"]


def test_findings_default_to_exact():
    assert Finding(source_id="1", answer="a").match == "exact"


def test_nearest_findings_are_labelled_in_the_fallback_answer(monkeypatch):
    async def no_followups(**kwargs):
        return []

    monkeypatch.setattr(llm_steps, "suggest_followup_from_findings", no_followups)
    ctx = deep_runner.RunContext(
        config=AgentConfig(litellm_base_url="http://unused"), complete=None, question="q"
    )
    ctx.findings.append(
        Finding(source_id="123", answer="1e-8 /s at 425 C", evidence="Table 1", match="nearest")
    )

    final = asyncio.run(
        deep_runner.build_final_result("q", json.dumps({"findings": []}), ctx)
    )

    assert final.status == "completed"
    assert "Nearest data reported (not at the exact conditions asked): Table 1" in final.answer
    assert "reported at other conditions" in final.answer


# --- prompts ---------------------------------------------------------------


def test_prompts_tell_each_role_about_nearest_findings():
    config = AgentConfig(litellm_base_url="http://unused")
    analyzer = " ".join(prompts.render_analyzer_prompt(config).split())
    verifier = " ".join(prompts.render_verifier_prompt(10, 12).split())
    orchestrator = " ".join(prompts.render_orchestrator_prompt(config).split())

    assert '"match"' in prompts.render_finding_schema()
    assert 'match: "nearest"' in analyzer and "Never adjust, interpolate" in analyzer
    assert 'A `match: "nearest"` candidate is not wrong' in verifier
    assert "DON'T GIVE UP EARLY" in orchestrator
    assert "does not use up the paper budget" in orchestrator


# --- search limits ---------------------------------------------------------


def test_search_limits_default_higher_and_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("MAX_OSTI_SEARCHES", raising=False)
    monkeypatch.delenv("MAX_SCHOLARLY_SEARCHES", raising=False)
    default = AgentConfig.from_env()
    monkeypatch.setenv("MAX_OSTI_SEARCHES", "8")
    monkeypatch.setenv("MAX_SCHOLARLY_SEARCHES", "6")
    tuned = AgentConfig.from_env()

    assert (default.max_osti_searches, default.max_scholarly_searches) == (5, 4)
    assert (tuned.max_osti_searches, tuned.max_scholarly_searches) == (8, 6)


def test_search_tools_use_the_configured_limits(monkeypatch):
    from research_agent import osti_client
    from research_agent.tools import RunContext
    from research_agent.tools.osti import build_osti_tools

    monkeypatch.setattr(osti_client, "search_osti", lambda query, k: [])
    ctx = RunContext(
        config=AgentConfig(litellm_base_url="http://unused", max_osti_searches=2),
        complete=None,
    )
    search = next(t for t in build_osti_tools(ctx) if t.name == "search_osti")

    for query in ("a", "b"):
        asyncio.run(search.ainvoke({"query": query}))
    third = json.loads(asyncio.run(search.ainvoke({"query": "c"})))

    assert "search limit reached (2)" in third["error"]


def test_orchestrator_states_the_limits_and_keeps_reading_after_searches_run_out():
    config = AgentConfig(
        litellm_base_url="http://unused", max_osti_searches=7, max_scholarly_searches=3
    )
    orchestrator = " ".join(prompts.render_orchestrator_prompt(config).split())

    assert "You have 7 OSTI and 3 scholarly searches per run" in orchestrator
    assert "Running out of searches is not a reason to stop" in orchestrator
    assert "up to 3 times" not in orchestrator


def test_analyzer_returns_the_closest_condition_however_far():
    analyzer = " ".join(
        prompts.render_analyzer_prompt(AgentConfig(litellm_base_url="http://unused")).split()
    )

    assert "however far its conditions are from those asked" in analyzer
    assert "never apply a fitted equation outside the range" in analyzer
