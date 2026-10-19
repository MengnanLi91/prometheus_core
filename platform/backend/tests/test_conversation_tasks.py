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

"""Offline tests for GET /api/conversations/{id}/tasks.

Ownership goes through a fake ``ConversationStateClient``; the Task Graph is seeded
into the autouse ``fake_redis`` in the client-agent's key format
(``tasks:graph`` / ``tasks:roots`` / ``tasks:node``, see
agents/client-agent/task_graph.py), so the async readers in ``app.task_events`` run
against real Redis commands.
"""

import json

from fastapi.testclient import TestClient

from app.routers import conversations as conversations_router
from tests.test_conversations import _authed_client, _detail_client

CONV = "c1"


def _node(task_id: str, *, parent: str | None = None, children: tuple[str, ...] = (), **extra) -> dict:
    """A node dict shaped like the client-agent's ``TaskNode.to_dict()``."""
    return {
        "task_id": task_id,
        "conversation_id": CONV,
        "parent_task_id": parent,
        "child_task_ids": list(children),
        "agent": None,
        "skill": None,
        "title": task_id,
        "own_state": "completed",
        "state": "completed",
        "is_background": False,
        "waiting_on": [],
        "fail_fast": True,
        "created_at": "2026-10-15T18:00:00+00:00",
        "started_at": "2026-10-15T18:00:00+00:00",
        "updated_at": "2026-10-15T18:00:01+00:00",
        "finished_at": "2026-10-15T18:00:01+00:00",
        "last_status_message": "",
        "progress": None,
        "result_ref": None,
        "error": None,
        **extra,
    }


async def _seed(redis, nodes: list[dict], roots: list[str], *, raw: dict[str, str] | None = None) -> None:
    for node in nodes:
        await redis.sadd(f"tasks:graph:{CONV}", node["task_id"])
        await redis.set(f"tasks:node:{CONV}:{node['task_id']}", json.dumps(node))
    for tid, blob in (raw or {}).items():
        await redis.sadd(f"tasks:graph:{CONV}", tid)
        await redis.set(f"tasks:node:{CONV}:{tid}", blob)
    if roots:
        await redis.rpush(f"tasks:roots:{CONV}", *roots)


def _owned_client(monkeypatch) -> TestClient:
    _detail_client(monkeypatch, conversations={CONV: {}}, owners={CONV: "alice"})
    return _authed_client(monkeypatch)


def test_tasks_requires_auth():
    from app.main import app

    resp = TestClient(app).get(f"/api/conversations/{CONV}/tasks")
    assert resp.status_code == 401


def test_tasks_404_when_not_owned(monkeypatch):
    _detail_client(monkeypatch, conversations={CONV: {}}, owners={CONV: "bob"})
    client = _authed_client(monkeypatch)  # token for "alice"

    assert client.get(f"/api/conversations/{CONV}/tasks").status_code == 404
    assert client.get("/api/conversations/missing/tasks").status_code == 404


def test_tasks_404_when_soft_deleted(monkeypatch):
    fake = _detail_client(monkeypatch, conversations={CONV: {}}, owners={CONV: "alice"})
    fake.set_hidden(CONV, True)
    client = _authed_client(monkeypatch)

    assert client.get(f"/api/conversations/{CONV}/tasks").status_code == 404


def test_tasks_empty_graph(monkeypatch):
    client = _owned_client(monkeypatch)

    resp = client.get(f"/api/conversations/{CONV}/tasks")
    assert resp.status_code == 200
    assert resp.json() == {"available": True, "conversation_id": CONV, "tasks": []}


async def test_tasks_returns_nested_tree(monkeypatch, fake_redis):
    root = _node(
        "t_root",
        children=("t_skill",),
        title="create a svg of the name drew",
        state="working",
        own_state="working",
        is_background=True,
        finished_at=None,
    )
    skill = _node(
        "t_skill",
        parent="t_root",
        agent="artifact-debug-agent",
        skill="create_svg_artifact",
        state="working",
        own_state="working",
        progress=0.5,
        last_status_message="drawing",
        finished_at=None,
    )
    await _seed(fake_redis, [root, skill], ["t_root"])
    client = _owned_client(monkeypatch)

    body = client.get(f"/api/conversations/{CONV}/tasks").json()
    assert body["available"] is True
    [tree] = body["tasks"]
    assert tree["task_id"] == "t_root"
    assert tree["state"] == "working"
    assert tree["is_background"] is True
    # Internal bookkeeping fields stay off the wire; children are nested instead.
    assert "child_task_ids" not in tree and "conversation_id" not in tree and "fail_fast" not in tree
    [child] = tree["children"]
    assert child["task_id"] == "t_skill"
    assert child["parent_task_id"] == "t_root"
    assert child["agent"] == "artifact-debug-agent"
    assert child["skill"] == "create_svg_artifact"
    assert child["progress"] == 0.5
    assert child["last_status_message"] == "drawing"
    assert child["children"] == []


async def test_tasks_roots_newest_first(monkeypatch, fake_redis):
    old = _node("t_old", created_at="2026-10-15T17:00:00+00:00")
    new = _node("t_new", created_at="2026-10-15T18:00:00+00:00")
    await _seed(fake_redis, [old, new], ["t_old", "t_new"])
    client = _owned_client(monkeypatch)

    tasks = client.get(f"/api/conversations/{CONV}/tasks").json()["tasks"]
    assert [t["task_id"] for t in tasks] == ["t_new", "t_old"]


async def test_tasks_include_orphans_and_skip_bad_nodes(monkeypatch, fake_redis):
    # t_orphan has no parent and is missing from tasks:roots; t_gone is listed as a
    # child but its node has expired; t_bad is corrupt JSON.
    root = _node("t_root", children=("t_gone",), created_at="2026-10-15T18:00:00+00:00")
    orphan = _node("t_orphan", created_at="2026-10-15T17:00:00+00:00")
    await _seed(fake_redis, [root, orphan], ["t_root"], raw={"t_bad": "{not json"})
    client = _owned_client(monkeypatch)

    tasks = client.get(f"/api/conversations/{CONV}/tasks").json()["tasks"]
    assert [t["task_id"] for t in tasks] == ["t_root", "t_orphan"]
    assert tasks[0]["children"] == []


async def test_tasks_cycle_renders_each_node_once(monkeypatch, fake_redis):
    a = _node("t_a", children=("t_b",))
    b = _node("t_b", parent="t_a", children=("t_a",))
    await _seed(fake_redis, [a, b], ["t_a"])
    client = _owned_client(monkeypatch)

    [tree] = client.get(f"/api/conversations/{CONV}/tasks").json()["tasks"]
    assert tree["task_id"] == "t_a"
    assert [c["task_id"] for c in tree["children"]] == ["t_b"]
    assert tree["children"][0]["children"] == []


def test_tasks_unavailable_when_valkey_down(monkeypatch):
    async def _boom(*_args, **_kwargs):
        raise ConnectionError("valkey down")

    monkeypatch.setattr(conversations_router, "build_task_tree", _boom)
    client = _owned_client(monkeypatch)

    resp = client.get(f"/api/conversations/{CONV}/tasks")
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "conversation_id": CONV, "tasks": []}
