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

"""Async Redis pub/sub helpers for background-task notifications.

When the client-agent detaches a slow turn it returns a ``backgrounded`` status
and keeps the orchestration running; on completion it publishes a state-change
event on ``tasks:events:{conversation_id}`` and persists the final response under
``tasks:result:{conversation_id}:{task_id}``. The frontend subscribes here and
renders the result out-of-band when the task finishes.

Uses ``redis.asyncio`` so the listener never blocks the service event loop. Every
function takes the process-wide client from :mod:`app.redis_client` rather than a URL:
these used to build a fresh client per call and ``aclose()`` it in ``finally``, which
cost a TCP connect and teardown on every round trip.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from app.redis_client import RedisClient, close_pubsub

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"completed", "failed", "canceled", "rejected"})


def events_channel(conversation_id: str) -> str:
    return f"tasks:events:{conversation_id}"


def _node_key(conversation_id: str, task_id: str) -> str:
    return f"tasks:node:{conversation_id}:{task_id}"


def _result_key(conversation_id: str, task_id: str) -> str:
    return f"tasks:result:{conversation_id}:{task_id}"


def _graph_key(conversation_id: str) -> str:
    return f"tasks:graph:{conversation_id}"


def _roots_key(conversation_id: str) -> str:
    return f"tasks:roots:{conversation_id}"


def is_terminal(state: Optional[str]) -> bool:
    return str(state or "") in TERMINAL_STATES


async def _get_json(client: Any, key: str) -> Optional[Dict[str, Any]]:
    try:
        data = await client.get(key)
    except Exception as exc:
        logger.warning("[TASKEVENTS] get %s failed: %s", key, exc)
        return None
    if not data:
        return None
    try:
        parsed: Dict[str, Any] = json.loads(data)
        return parsed
    except Exception:
        return None


async def fetch_result(redis: RedisClient, conversation_id: str, task_id: str) -> Optional[Dict[str, Any]]:
    """Fetch the persisted final response payload for a completed task."""
    return await _get_json(redis, _result_key(conversation_id, task_id))


async def node_snapshot(
    redis: RedisClient,
    conversation_id: str,
    task_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the current persisted Task Graph node, or ``None``.

    Used as a subscribe-race guard: a task that reached a terminal state between
    detach and subscribe won't emit another event, so callers check the node
    directly once on startup. The latest status message also seeds the live
    background-progress display.
    """
    return await _get_json(redis, _node_key(conversation_id, task_id))


async def node_state(redis: RedisClient, conversation_id: str, task_id: str) -> Optional[str]:
    """Return only the current persisted state of a Task Graph node."""
    node = await node_snapshot(redis, conversation_id, task_id)
    return str(node.get("state")) if node else None


async def list_graph_nodes(redis: RedisClient, conversation_id: str) -> Dict[str, Dict[str, Any]]:
    """Return every persisted Task Graph node of a conversation, keyed by task id.

    One ``SMEMBERS`` plus one ``MGET``. A missing (expired) or corrupt node is skipped.
    Unlike :func:`node_snapshot`, a Valkey failure propagates, so the caller can tell
    "Valkey is down" apart from "no tasks".
    """
    task_ids = sorted(str(tid) for tid in await redis.smembers(_graph_key(conversation_id)))
    if not task_ids:
        return {}
    blobs = await redis.mget([_node_key(conversation_id, tid) for tid in task_ids])
    nodes: Dict[str, Dict[str, Any]] = {}
    for tid, data in zip(task_ids, blobs):
        if not data:
            continue
        try:
            node = json.loads(data)
        except Exception:
            logger.warning("[TASKEVENTS] Corrupt task node %s/%s", conversation_id, tid)
            continue
        if isinstance(node, dict):
            nodes[str(node.get("task_id") or tid)] = node
    return nodes


async def build_task_tree(redis: RedisClient, conversation_id: str) -> List[Dict[str, Any]]:
    """Return a conversation's root task nodes, each with its ``children`` nested.

    An async port of the client-agent's ``task_graph.build_tree``
    (agents/client-agent/task_graph.py), which backs the ``/tasks`` slash command;
    keep the two in step. Roots come from ``tasks:roots:{conv}`` in creation order,
    then any node with no parent that the list misses, so nothing is hidden. A node
    is rendered at most once, which also stops a corrupt parent/child cycle.
    """
    nodes = await list_graph_nodes(redis, conversation_id)
    if not nodes:
        return []
    root_ids = [str(r) for r in await redis.lrange(_roots_key(conversation_id), 0, -1)]

    seen: set[str] = set()

    def render(task_id: str) -> Optional[Dict[str, Any]]:
        if task_id in seen or task_id not in nodes:
            return None
        seen.add(task_id)
        node = nodes[task_id]
        return {
            **node,
            "children": [child for cid in node.get("child_task_ids") or [] if (child := render(str(cid))) is not None],
        }

    roots = [r for r in root_ids if r in nodes]
    roots += [tid for tid, n in nodes.items() if not n.get("parent_task_id") and tid not in roots]
    return [tree for r in roots if (tree := render(r)) is not None]


async def subscribe(redis: RedisClient, conversation_id: str) -> AsyncIterator[Dict[str, Any]]:
    """Yield Task Graph events for a conversation until the consumer is cancelled.

    Each yielded value is a parsed event dict published on
    ``tasks:events:{conversation_id}`` (``{type, task_id, state, …}``). A Valkey failure
    propagates to the caller, which logs and degrades to "no proactive notifications"
    rather than crashing the SSE stream (see ``services/events.run_task_subscriber``).

    The single long-lived subscriber per session is started when the session's
    SSE events stream is opened and cancelled when it closes. ``pubsub()`` takes its own
    connection from the shared pool, so this does not block other callers of the client.
    """
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(events_channel(conversation_id))
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                event = json.loads(msg["data"])
            except Exception:
                continue
            if isinstance(event, dict):
                yield event
    finally:
        # The client itself is process-wide and stays open; only this subscription
        # (and the connection it borrowed from the pool) is released here.
        await close_pubsub(pubsub, events_channel(conversation_id))
