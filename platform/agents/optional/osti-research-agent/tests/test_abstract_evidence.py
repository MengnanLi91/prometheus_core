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

"""Abstract evidence: code-checked statements from the abstracts of papers
whose full text could not be read, kept apart from verified findings."""

import asyncio
import json

import pytest

from research_agent import llm_steps
from research_agent.core import deep_runner
from research_agent.core.config import AgentConfig
from research_agent.core.events import Finding
from research_agent.tools import RunContext, build_tools
from research_agent.tools import evidence

ABSTRACT = (
    "Thermal creep of type 316 stainless steel was measured at 550 °C under "
    "120 MPa; the minimum creep rate was 3.2e-9 /s, and creep was negligible "
    "below 450 °C."
)


def _ctx():
    ctx = RunContext(config=AgentConfig(litellm_base_url="http://unused"), complete=None)
    ctx.search_hits["3729583"] = {
        "title": "Creep of 316 SS", "url": "https://www.osti.gov/biblio/3729583",
        "description": ABSTRACT,
    }
    ctx.download_failures["3729583"] = "PDF not available for download"
    return ctx


def _record(ctx, **args):
    values = {"source_id": "3729583", "statement": "", "quote": ""}
    values.update(args)
    tool = next(t for t in build_tools(ctx) if t.name == "record_abstract_evidence")
    return json.loads(asyncio.run(tool.ainvoke(values)))


QUOTE = "the minimum creep rate was 3.2e-9 /s"


def test_a_supported_statement_is_kept():
    ctx = _ctx()
    out = _record(ctx, statement="Minimum creep rate of 3.2e-9 /s", quote=QUOTE)

    assert out["ok"] is True
    assert ctx.abstract_evidence[0]["quote"] == QUOTE
    assert ctx.findings == []  # never a finding


@pytest.mark.parametrize(
    "statement, quote, error",
    [
        # Even a material grade counts: every number must be in the quote.
        ("Rate of 3.2e-9 /s for 316 SS", QUOTE, "numbers not in the cited text: 316"),
        ("Rate of 3.2e-9 /s", "the minimum creep rate is 3.2e-9 /s", "not word for word"),
        ("Rate of 4.0e-9 /s", QUOTE, "numbers not in the cited text: 4.0e-9"),
        ("At 400 °C the rate was 3.2e-9 /s", QUOTE, "400"),
        ("", QUOTE, "both required"),
    ],
)
def test_unsupported_statements_are_refused(statement, quote, error):
    ctx = _ctx()
    out = _record(ctx, statement=statement, quote=quote)

    assert out["ok"] is False and error in out["error"]
    assert ctx.abstract_evidence == []


def test_a_number_with_a_changed_unit_is_refused():
    quote = "measured at 550 °C under 120 MPa"
    assert evidence.supported_by("550 °C and 120 MPa", quote) is None
    assert "120 ksi" in evidence.supported_by("550 °C and 120 ksi", quote)


def test_only_for_papers_whose_full_text_could_not_be_read():
    ctx = _ctx()
    del ctx.download_failures["3729583"]  # not tried yet: could be readable

    out = _record(ctx, statement="Minimum creep rate of 3.2e-9 /s", quote=QUOTE)

    assert out["ok"] is False and "may be readable" in out["error"]


def test_not_for_a_paper_that_already_has_a_finding():
    ctx = _ctx()
    ctx.findings.append(Finding(source_id="3729583", answer="a"))

    out = _record(ctx, statement="Minimum creep rate of 3.2e-9 /s", quote=QUOTE)

    assert out["ok"] is False and "verified finding" in out["error"]


def test_a_scholarly_result_without_a_pdf_counts_as_unreadable():
    ctx = _ctx()
    ctx.search_hits["10.1_x"] = {"origin": "openalex", "oa_pdf_url": None, "abstract": ABSTRACT}

    out = _record(ctx, source_id="10.1_x", statement="Rate of 3.2e-9 /s", quote=QUOTE)

    assert out["ok"] is True


def test_unknown_source_is_refused():
    out = _record(_ctx(), source_id="999", statement="x", quote="x")

    assert out["ok"] is False and "unknown source_id" in out["error"]


def _final(ctx, monkeypatch):
    async def no_followups(**kwargs):
        return []

    monkeypatch.setattr(llm_steps, "suggest_followup_from_findings", no_followups)
    return asyncio.run(
        deep_runner.build_final_result("q", json.dumps({"findings": []}), ctx)
    )


def test_abstract_evidence_gets_its_own_labelled_section(monkeypatch):
    ctx = _ctx()
    ctx.findings.append(Finding(source_id="111", answer="a", evidence="Table 1"))
    _record(ctx, statement="Minimum creep rate of 3.2e-9 /s", quote=QUOTE)

    final = _final(ctx, monkeypatch)

    head, abstract_part = final.answer.split("## From abstracts (full text not available)")
    assert "Table 1" in head and QUOTE not in head
    assert f'"{QUOTE}"' in abstract_part
    assert "[Creep of 316 SS](https://www.osti.gov/biblio/3729583)" in abstract_part
    assert any(p.get("evidence") == "abstract" for p in final.provenance)
    assert final.status == "completed"


def test_abstract_evidence_alone_stays_no_results_but_is_shown(monkeypatch):
    ctx = _ctx()
    _record(ctx, statement="Minimum creep rate of 3.2e-9 /s", quote=QUOTE)

    final = _final(ctx, monkeypatch)

    assert final.status == "no_results"
    assert "## From abstracts" in final.answer and QUOTE in final.answer


def test_abstract_evidence_is_not_cached_as_an_answer(monkeypatch):
    cached = []

    class Knowledge:
        async def cache_answer(self, question, answer, sources):
            cached.append(answer)

    ctx = _ctx()
    ctx.knowledge = Knowledge()
    ctx.findings.append(Finding(source_id="111", answer="a", evidence="Table 1"))
    _record(ctx, statement="Minimum creep rate of 3.2e-9 /s", quote=QUOTE)

    _final(ctx, monkeypatch)

    assert cached and "From abstracts" not in cached[0]


def test_the_orchestrator_is_told_how_to_use_it():
    from research_agent.core import prompts

    prompt = prompts.render_orchestrator_prompt(AgentConfig(litellm_base_url="http://u"))

    assert "record_abstract_evidence(source_id, statement, quote)" in prompt
    assert "never put it in `findings`" in prompt
