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

"""ResearchRunner: the event-stream boundary between execution engines and
callers such as the A2A executor and CLI.

Bridges pipeline.answer_question's emit-callback progress reporting to an
async generator of AgentEvents, and enforces the overall time budget as an
outer safety net - the pipeline's own internal timeout (perform_literature_search's
check_timeout, ~14 minutes) is expected to fire first in normal operation;
this one is for the case where something hangs entirely.
"""

import asyncio
import logging
import time
from typing import AsyncIterator, Dict, List, Optional

import redis

from research_agent import pipeline
from research_agent.core.config import AgentConfig
from research_agent.core.events import AgentEvent, FinalResult, Finding, ProgressEvent
from research_agent.core.llm import CompleteFn, LiteLLMClient
from research_agent.memory import PdfCache, RedisCaches, TextCache

logger = logging.getLogger(__name__)


class ResearchRunner:
    """Runs the research pipeline and streams its progress as AgentEvents."""

    def __init__(
        self,
        config: AgentConfig,
        complete: Optional[CompleteFn] = None,
        redis_client: Optional[redis.Redis] = None,
        pdf_cache: Optional[PdfCache] = None,
        text_cache: Optional[TextCache] = None,
    ):
        self.config = config
        route = config.model_route("orchestrator")
        self._complete = (
            complete
            or LiteLLMClient(
                route.base_url,
                route.api_key,
                route.model,
            ).complete
        )

        # Only build a RedisCaches when the caller didn't already hand us one
        # (the A2A executor reuses its own) and config actually has a Redis URL
        # (the CLI's --no-redis mode leaves this None -> everything degrades
        # gracefully, per research_agent.memory's Optional[redis.Redis] handling).
        caches = (
            RedisCaches(config.redis_url, config.redis_password)
            if config.redis_url
            else None
        )
        self._redis_client = (
            redis_client
            if redis_client is not None
            else (caches.client if caches else None)
        )
        self._pdf_cache = pdf_cache if pdf_cache is not None else caches
        self._text_cache = text_cache if text_cache is not None else caches

    async def run(self, question: str, *, run_id: str) -> AsyncIterator[AgentEvent]:
        """Dispatch on the configured engine. Both engines stream the same
        AgentEvents behind the same outer-budget safety net; only the job that
        produces them and the shape of the salvaged partial differ."""
        if self.config.engine == "deepagent":
            gen = self._run_deep(question, run_id=run_id)
        else:
            gen = self._run_pipeline(question, run_id=run_id)
        async for event in gen:
            yield event

    async def _run_pipeline(
        self, question: str, *, run_id: str
    ) -> AsyncIterator[AgentEvent]:
        q: asyncio.Queue = asyncio.Queue()
        findings: List[Dict] = []

        async def emit(message: str, **detail) -> None:
            await q.put(ProgressEvent(message=message, detail=detail or None))

        async def _job() -> None:
            try:
                result = await pipeline.answer_question(
                    question,
                    self._complete,
                    emit,
                    self._redis_client,
                    self._pdf_cache,
                    self._text_cache,
                    findings=findings,
                    config=self.config,
                    caches=self._pdf_cache,
                )
                await q.put(_dict_to_final_result(question, result))
            except Exception as e:
                logger.error(f"[{run_id}] Pipeline failed: {e}", exc_info=True)
                await q.put(
                    FinalResult(status="error", question=question, answer=str(e))
                )
            finally:
                await q.put(None)

        async for event in self._drain(
            q, _job, run_id, lambda: _timeout_result(question, findings)
        ):
            yield event

    async def _run_deep(
        self, question: str, *, run_id: str
    ) -> AsyncIterator[AgentEvent]:
        # Imported lazily: the deep engine pulls in langchain/deepagents, which
        # the pipeline engine (and pipeline-only deployments) must not require.
        from research_agent.core.deep_runner import run_deep

        q: asyncio.Queue = asyncio.Queue()
        findings: List[Finding] = []
        deadline = time.monotonic() + max(self.config.total_budget_s - 30, 0)

        async def _job() -> None:
            try:
                async for event in run_deep(
                    question,
                    self.config,
                    run_id,
                    complete=self._complete,
                    redis_client=self._redis_client,
                    caches=self._pdf_cache,
                    pdf_cache=self._pdf_cache,
                    findings=findings,
                    deadline=deadline,
                ):
                    await q.put(event)
            except Exception as e:
                logger.error(f"[{run_id}] Deep agent failed: {e}", exc_info=True)
                await q.put(
                    FinalResult(status="error", question=question, answer=str(e))
                )
            finally:
                await q.put(None)

        async for event in self._drain(
            q, _job, run_id, lambda: _deep_timeout_result(question, findings)
        ):
            yield event

    async def _drain(
        self,
        q: asyncio.Queue,
        job,
        run_id: str,
        on_timeout,
    ) -> AsyncIterator[AgentEvent]:
        """Run ``job`` as a task, forwarding events off ``q`` until it signals
        completion with ``None``, and enforce the outer time budget - on
        exhaustion, cancel the task and yield ``on_timeout()`` (the salvaged
        partial for whichever engine is running)."""
        task = asyncio.create_task(job())
        deadline = time.monotonic() + max(self.config.total_budget_s - 30, 0)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(f"[{run_id}] Budget exhausted, cancelling job")
                    task.cancel()
                    yield on_timeout()
                    return
                try:
                    event = await asyncio.wait_for(q.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    logger.warning(f"[{run_id}] Budget exhausted, cancelling job")
                    task.cancel()
                    yield on_timeout()
                    return

                if event is None:
                    return
                yield event
        finally:
            if not task.done():
                task.cancel()


def _dict_to_final_result(question: str, result: Dict) -> FinalResult:
    return FinalResult(
        status=result.get("status", "completed"),
        question=result.get("question", question),
        answer=result.get("answer", ""),
        provenance=result.get("provenance", []),
        search_query=result.get("search_query"),
        papers_assessed=result.get("papers_assessed", 0),
        relevant_papers=result.get("relevant_papers", 0),
        papers_analyzed=result.get("papers_analyzed", 0),
        facts_stored=result.get("facts_stored", 0),
        suggested_queries=result.get("suggested_queries", []),
        progress_log=result.get("progress_log", []),
        cached=result.get("cached", False),
        source=result.get("source"),
    )


def _timeout_result(question: str, findings: List[Dict]) -> FinalResult:
    if findings:
        answer = (
            f"Research timed out after reaching the time budget. Found partial "
            f"information from {len(findings)} paper(s) before timing out."
        )
    else:
        answer = "Research timed out after reaching the time budget before finding any results."

    provenance = [
        {
            "source": f"OSTI #{f.get('osti_id', '')}",
            "title": f.get("title", ""),
            "url": f.get("url", ""),
            "excerpt": (f.get("excerpt") or "")[:500],
        }
        for f in findings
    ]

    return FinalResult(
        status="timeout_partial",
        question=question,
        answer=answer,
        provenance=provenance,
        papers_analyzed=len(findings),
    )


def _deep_timeout_result(question: str, findings: List[Finding]) -> FinalResult:
    """Salvage a partial result for the deep engine from the Findings captured
    off the stream so far. Titles/urls aren't on Finding (they live in the run
    context, which the runner doesn't hold), so provenance here is minimal."""
    if findings:
        answer = (
            f"Research timed out after reaching the time budget. Found partial "
            f"information from {len(findings)} paper(s) before timing out."
        )
    else:
        answer = "Research timed out after reaching the time budget before finding any results."

    provenance = [
        {
            "source": f"OSTI #{f.source_id}",
            "title": "",
            "url": "",
            "excerpt": (f.excerpt or f.evidence or "")[:500],
        }
        for f in findings
    ]

    return FinalResult(
        status="timeout_partial",
        question=question,
        answer=answer,
        provenance=provenance,
        findings=list(findings),
        papers_analyzed=len(findings),
    )
