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

"""Tests for the thin A2A driver in agent.py (status updates, task lifecycle).

Loaded via importlib path hacking because agent.py needs the full
prometheus_api/a2a runtime, unlike the research_agent modules which are
imported normally in the other test files.
"""

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

# agent.py imports prometheus_api/a2a, which only exist in this agent's Docker
# base image (see pyproject.toml). Outside the container the whole test is a
# skip, not a failure - the research_agent core is covered by the other files.
_HAS_A2A_RUNTIME = (
    importlib.util.find_spec("prometheus_api") is not None
    and importlib.util.find_spec("a2a") is not None
)

pytestmark = pytest.mark.skipif(
    not _HAS_A2A_RUNTIME,
    reason="agent.py requires the prometheus_api/a2a runtime (Docker base image only)",
)


def _load_agent_module():
    module_path = Path(__file__).resolve().parents[1] / "agent.py"
    spec = importlib.util.spec_from_file_location("osti_agent_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_status_updates_use_a2a_v1_task_states():
    mod = _load_agent_module()
    agent = object.__new__(mod.OSTIResearchAgent)
    sent_statuses = []

    agent._store_status_in_redis = lambda *args, **kwargs: None

    async def capture_status_update(**kwargs):
        sent_statuses.append(kwargs)

    agent.send_status_update = capture_status_update
    context = SimpleNamespace(context_id="context-1")
    event_queue = object()

    asyncio.run(agent._send_status(context, event_queue, "Searching OSTI"))
    asyncio.run(
        agent._send_final_result(
            context,
            event_queue,
            {"status": "completed", "answer": "Research result"},
        )
    )

    assert sent_statuses[0]["state"] == mod.TaskState.TASK_STATE_WORKING
    assert sent_statuses[0]["final"] is False
    assert sent_statuses[1]["state"] == mod.TaskState.TASK_STATE_COMPLETED
    assert sent_statuses[1]["final"] is True


def _artifact_agent(mod, registered, sent):
    agent = object.__new__(mod.OSTIResearchAgent)

    def register(context, reference):
        reference.conversation_id = "conv-1"
        registered.append(reference)
        return reference

    async def send_text(context, event_queue, *, text, name, artifact_id):
        sent.append({"text": text, "name": name, "artifact_id": artifact_id})

    agent.register_artifact_reference = register
    agent.send_text_artifact = send_text
    return agent


def _search_result(mod):
    from research_agent.core.events import FinalResult, Finding

    return FinalResult(
        status="completed",
        question="Does Ti creep?",
        searched_papers={
            "123": {"title": "Creep of Ti", "url": "https://www.osti.gov/biblio/123"},
            "456": {"title": "Unused", "url": "https://www.osti.gov/biblio/456"},
        },
        findings=[Finding(source_id="123", answer="creeps above 800 C")],
    )


def test_search_artifacts_are_registered_and_sent_under_one_id():
    mod = _load_agent_module()
    registered, sent = [], []
    agent = _artifact_agent(mod, registered, sent)

    asyncio.run(agent._send_search_artifacts(object(), object(), _search_result(mod)))

    listing, card = registered
    assert [s["artifact_id"] for s in sent] == [listing.artifact_id, card.artifact_id]
    assert [s["text"] for s in sent] == [listing.text_body, card.text_body]
    assert listing.mime_type == "text/markdown" and "Unused" in listing.text_body
    assert card.uri == "https://www.osti.gov/biblio/123"
    assert card.mime_type is None and "reference" in card.tags
    assert card.metadata == {"source": "OSTI · 123"}


def test_search_artifacts_route_to_report_and_reference_viewers():
    backend = Path(__file__).resolve().parents[4] / "backend"
    if not (backend / "app" / "services" / "artifacts.py").exists():
        pytest.skip("backend sources not available")
    import sys

    sys.path.insert(0, str(backend))
    try:
        from app.services.artifacts import classify_render_hint
    except ImportError as exc:
        pytest.skip(f"backend dependencies not installed: {exc}")
    finally:
        sys.path.remove(str(backend))

    mod = _load_agent_module()
    registered, sent = [], []
    agent = _artifact_agent(mod, registered, sent)
    asyncio.run(agent._send_search_artifacts(object(), object(), _search_result(mod)))

    hints = [classify_render_hint(r.to_dict()) for r in registered]
    assert hints == ["report", "reference"]


def test_search_artifact_failure_does_not_fail_the_run():
    mod = _load_agent_module()
    agent = object.__new__(mod.OSTIResearchAgent)

    def broken_register(context, reference):
        raise ConnectionError("redis down")

    agent.register_artifact_reference = broken_register
    asyncio.run(agent._send_search_artifacts(object(), object(), _search_result(mod)))
