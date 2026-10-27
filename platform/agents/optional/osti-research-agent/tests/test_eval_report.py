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

"""The evaluation CLI: question filters, the reports it writes, and its output.
The engine is faked, so nothing here needs a model, Redis, or the network."""

import asyncio
import json

import pytest

from evals import __main__ as cli
from evals import report as render
from evals import run as eval_run
from evals import scoring
from research_agent.core.config import AgentConfig

QUESTIONS = """questions:
  - id: ss316-creep
    question: What is the creep rate of SS316?
    category: core
    expected_facts: ["316"]
  - id: nonsense
    question: What is the melting point of unobtainium-42?
    category: adversarial
    expect_status: no_results
  - id: uq-one
    question: Which inputs dominate the uncertainty?
    category: uq
"""

RESULTS = {
    "ss316-creep": {"status": "completed", "answer": "SS316 creeps [Paper 1](http://a)",
                    "provenance": [{"source": "OSTI #1"}]},
    "nonsense": {"status": "completed", "answer": "invented", "provenance": []},
    "uq-one": {"status": "completed", "answer": "Paper 1 says x", "provenance": [{"s": 1}]},
}


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(
        eval_run, "_load_agent_config", lambda: AgentConfig(litellm_base_url="http://u")
    )
    seen = []

    async def run_engine(engine, specs, base, workdir, redis_db, on_record=None, **kwargs):
        records = []
        for spec in specs:
            seen.append(spec["id"])
            result = {**RESULTS[spec["id"]], "_engine": engine, "_wall_s": 75.0}
            record = scoring.score_result(result, spec)
            records.append(record)
            if on_record:
                on_record(record)
        return records

    monkeypatch.setattr(eval_run, "_run_engine", run_engine)
    return seen


def _main(tmp_path, *extra):
    questions = tmp_path / "questions.yaml"
    questions.write_text(QUESTIONS)
    args = ["--questions", str(questions), "--engines", "deepagent",
            "--report", str(tmp_path / "r" / "report.json"),
            "--workdir", str(tmp_path / "r" / "out"), "--no-judge", *extra]
    return asyncio.run(eval_run._main(args))


def test_run_writes_json_and_markdown_and_prints_a_summary(tmp_path, fake_engine, capsys):
    assert _main(tmp_path) == 0

    report = json.loads((tmp_path / "r" / "report.json").read_text())
    assert report["complete"] is True and report["questions"] == 3
    markdown = (tmp_path / "r" / "report.md").read_text()
    assert "## Failures (2)" in markdown
    assert "[nonsense](out/deepagent/nonsense/result.json)" in markdown
    assert "What is the melting point of unobtainium-42?" in markdown

    out = capsys.readouterr().out
    assert "deepagent  1/3" in out
    assert "[deepagent] nonsense: status 'completed' not the expected one; " \
           "completed with no sources" in out
    assert "[deepagent] uq-one: citation_link_rate 0.00" in out


def test_filters_by_id_category_and_limit(tmp_path, fake_engine):
    _main(tmp_path, "--category", "core", "--category", "uq")
    assert fake_engine == ["ss316-creep", "uq-one"]

    fake_engine.clear()
    _main(tmp_path, "--id", "nonsense")
    assert fake_engine == ["nonsense"]

    fake_engine.clear()
    _main(tmp_path, "--limit", "1")
    assert fake_engine == ["ss316-creep"]


def test_unknown_id_or_empty_selection_stops_before_running(tmp_path, fake_engine):
    with pytest.raises(SystemExit, match="unknown question id"):
        _main(tmp_path, "--id", "typo")
    with pytest.raises(SystemExit, match="no questions match"):
        _main(tmp_path, "--category", "nope")
    assert fake_engine == []


def test_report_is_rewritten_after_every_question(tmp_path, monkeypatch):
    monkeypatch.setattr(
        eval_run, "_load_agent_config", lambda: AgentConfig(litellm_base_url="http://u")
    )
    report_path = tmp_path / "r" / "report.json"

    async def dies_after_one(engine, specs, base, workdir, redis_db, on_record=None, **kwargs):
        spec = specs[0]
        on_record(scoring.score_result({**RESULTS[spec["id"]], "_engine": engine}, spec))
        raise RuntimeError("container restarted")

    monkeypatch.setattr(eval_run, "_run_engine", dies_after_one)
    with pytest.raises(RuntimeError):
        _main(tmp_path)

    partial = json.loads(report_path.read_text())
    assert partial["complete"] is False and len(partial["records"]) == 1


def test_report_command_reprints_a_saved_report(tmp_path, fake_engine, capsys):
    _main(tmp_path)
    capsys.readouterr()

    assert cli.main(["report", str(tmp_path / "r" / "report.json")]) == 0
    assert "OSTI evaluation: 3 question(s)" in capsys.readouterr().out
    assert cli.main(["report", str(tmp_path / "missing.json")]) == 1


def test_cli_rejects_unknown_command(capsys):
    assert cli.main(["nope"]) == 2
    assert cli.main([]) == 2


def test_failures_explain_each_check():
    record = {"status": "error", "status_match": False, "provenance_ok": True,
              "citation_link_rate": 1.0, "source_hit_rate": 0.5, "fact_match": 1.0}

    assert render.failures(record) == [
        "status 'error' not the expected one",
        "source_hit_rate 0.50",
    ]
    assert render.failures({**record, "status_match": True, "source_hit_rate": 1.0}) == []


def test_markdown_escapes_table_breaking_text():
    report = {"questions": 1, "engines": ["deepagent"], "records": [],
              "aggregate": {}}
    markdown = render.markdown_report(report, questions={"q": "a | b"})

    assert "## Failures (0)" in markdown and "Every question passed" in markdown
    assert render._md_cell("a | b\nc") == "a \\| b c"
