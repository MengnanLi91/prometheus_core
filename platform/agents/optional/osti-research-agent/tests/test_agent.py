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
