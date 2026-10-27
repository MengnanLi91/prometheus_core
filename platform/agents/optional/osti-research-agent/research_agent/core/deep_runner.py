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

"""Deep-agent engine: drive the ``create_deep_agent`` graph and translate its
LangGraph stream into the same ``AgentEvent`` stream the pipeline engine emits,
so the A2A executor and CLI are unchanged.

- ``custom`` stream chunks (emitted by the tools via ``get_stream_writer``)
  become ``ProgressEvent``s directly.
- ``updates`` chunks (node state deltas) are translated into human-readable
  tool-call statuses and todo updates. The ``analyze_paper`` tool returns only
  verified Findings, which are captured into ``ctx.findings`` so the
  runner can salvage a ``timeout_partial`` if the outer budget fires.
- The orchestrator's final JSON is parsed into a ``FinalResult`` with linkified
  provenance, reusing ``citations`` and ``llm_steps`` from the pipeline engine.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from typing import AsyncIterator, Dict, Iterator, List, Optional

from langchain_core.messages import AIMessage, ToolMessage

from research_agent import citations, llm_steps
from research_agent.core.config import AgentConfig
from research_agent.core.deep_agent import build_research_agent
from research_agent.core.events import AgentEvent, FinalResult, Finding, ProgressEvent
from research_agent.core.llm import CompleteFn
from research_agent.tools import RunContext

logger = logging.getLogger(__name__)

RECURSION_LIMIT = 80

# tool name -> human-readable status shown in the UI (matches the tone of the
# pipeline engine's emit() messages).
_TOOL_STATUS = {
    "search_osti": "Searching OSTI database...",
    "search_scholarly": "Searching open literature (OpenAlex/arXiv)...",
    "search_web": "Searching the web...",
    "download_paper": "Downloading paper...",
    "parse_paper": "Parsing paper (tables + figures)...",
    "analyze_paper": "Analyzing and verifying paper...",
    "search_knowledge_base": "Checking knowledge base...",
    "store_facts": "Storing facts in knowledge base...",
    "write_todos": "Updating plan...",
}


def _tool_call_status(name: str, args: dict) -> str:
    base = _TOOL_STATUS.get(name, f"Running {name}...")
    query = args.get("query") if isinstance(args, dict) else None
    if query and name == "search_osti":
        return f"Searching OSTI for: {query}"
    if query and name == "search_scholarly":
        return f"Searching open literature for: {query}"
    if query and name == "search_web":
        return f"Searching the web for: {query}"
    return base


def _capture_finding(content: str, ctx: RunContext) -> None:
    """Capture the verified result returned by ``analyze_paper``."""
    parsed = llm_steps.extract_json_from_text(content)
    if not isinstance(parsed, dict):
        return
    if parsed.get("answered") is False:
        return
    if not parsed.get("source_id") or not parsed.get("answer"):
        return
    source_id = str(parsed["source_id"])
    if any(f.source_id == source_id for f in ctx.findings):
        return
    try:
        ctx.findings.append(
            Finding(
                source_id=source_id,
                answer=str(parsed.get("answer", "")),
                evidence=str(parsed.get("evidence", "") or ""),
                excerpt=parsed.get("excerpt"),
                artifacts=list(parsed.get("artifacts", []) or []),
            )
        )
    except (TypeError, ValueError) as e:
        logger.debug("[deep] could not coerce finding: %s", e)


def translate_update(chunk: Dict, ctx: RunContext) -> Iterator[ProgressEvent]:
    """Translate one ``updates`` chunk ({node: state_delta}) into ProgressEvents
    and capture any finding it carries."""
    if not isinstance(chunk, dict):
        return
    for node_out in chunk.values():
        if not isinstance(node_out, dict):
            continue
        todos = node_out.get("todos")
        if todos:
            yield ProgressEvent(
                kind="todo_update", message="Updated plan", detail={"todos": todos}
            )
        for msg in node_out.get("messages", []) or []:
            if isinstance(msg, AIMessage):
                for call in getattr(msg, "tool_calls", None) or []:
                    name = call.get("name", "") if isinstance(call, dict) else ""
                    args = call.get("args", {}) if isinstance(call, dict) else {}
                    yield ProgressEvent(
                        kind="tool_call",
                        message=_tool_call_status(name, args),
                        detail={"tool": name},
                    )
            elif isinstance(msg, ToolMessage):
                content = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                if getattr(msg, "name", None) == "analyze_paper":
                    _capture_finding(content, ctx)


def _message_text(msg) -> str:
    """Extract plain text from a message's ``content`` (str, or a list of content
    parts), independent of langchain_core version niceties like ``.text()``."""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(part.get("text", ""))
        return "".join(parts)
    return str(content or "")


def _last_ai_text(chunk: Dict) -> Optional[str]:
    """The most recent non-empty AI message text in an updates chunk, if any -
    tracked across the stream to recover the orchestrator's final message."""
    if not isinstance(chunk, dict):
        return None
    latest = None
    for node_out in chunk.values():
        if not isinstance(node_out, dict):
            continue
        for msg in node_out.get("messages", []) or []:
            if isinstance(msg, AIMessage):
                text = _message_text(msg).strip()
                if text:
                    latest = text
    return latest


def _findings_to_answers(findings: List[Finding], ctx: RunContext) -> List[Dict]:
    """Shape findings like the pipeline's ``answers_found`` dicts so the existing
    provenance/linkify helpers work unchanged; titles/urls come from the search
    hits recorded when the paper was found."""
    answers = []
    for f in findings:
        hit = ctx.search_hits.get(f.source_id, {})
        url = hit.get("url", "")
        if not url and f.source_id.isdigit():
            url = f"https://www.osti.gov/biblio/{f.source_id}"
        answers.append(
            {
                "osti_id": f.source_id,
                "title": hit.get("title", ""),
                "url": url,
                "excerpt": (f.excerpt or f.evidence or "")[:500],
                "answer": f.answer,
                "evidence": f.evidence,
            }
        )
    return answers


def _render_grounded_answer(answers: List[Dict]) -> str:
    """Render only claims already present in paper-analyzer findings."""
    sections = ["## Evidence-grounded findings"]
    for index, answer in enumerate(answers, 1):
        title = str(answer.get("title", "") or "").strip()
        heading = f"### Paper {index}"
        if title:
            heading += f": {title}"
        evidence = str(answer.get("evidence", "") or "").strip()
        grounded_text = evidence or str(answer.get("answer", "")).strip()
        sections.extend(
            [
                heading,
                f"{grounded_text} (Paper {index})",
            ]
        )

    sections.extend(
        [
            "## Limitations",
            "This answer is limited to the analyzed findings above; no estimate "
            "or extrapolation was added.",
        ]
    )
    return "\n\n".join(sections)


async def build_final_result(
    question: str, final_text: str, ctx: RunContext
) -> FinalResult:
    """Parse the orchestrator's final JSON into a FinalResult with linkified
    provenance. Falls back to ``status="error"`` (keeping the raw text) if the
    final message isn't parseable JSON."""
    parsed = llm_steps.extract_json_from_text(final_text or "")
    if not isinstance(parsed, dict) or "answer" not in parsed:
        logger.warning("[deep] final message was not parseable result JSON")
        return FinalResult(
            status="error",
            question=question,
            answer=(final_text or "The agent did not produce a final answer.").strip(),
            findings=list(ctx.findings),
            papers_analyzed=ctx.papers_analyzed,
        )

    # Only engine-verified findings are authoritative. The orchestrator's final
    # array is deliberately ignored because it can contain cached or invented
    # entries that never passed source-artifact verification.
    findings = list(ctx.findings)
    answers = _findings_to_answers(findings, ctx)
    if answers:
        # Do not ask another model to paraphrase the findings: even a
        # findings-only prompt can add unsupported domain knowledge.
        answer_text = _render_grounded_answer(answers)
        status = "completed"
    elif ctx.verification_errors:
        answer_text = (
            "The agent found candidate evidence, but source verification failed "
            "before any finding could be accepted."
        )
        status = "error"
    else:
        answer_text = (
            "No source-verified findings were available to answer the question."
        )
        status = "no_results"

    provenance, paper_to_provenance = citations.deduplicate_provenance_with_mapping(
        answers
    )
    answer_text = citations.linkify_paper_references(
        answer_text, provenance, paper_to_provenance
    )

    suggested_queries = await llm_steps.suggest_followup_queries(
        question=question,
        answer=answer_text,
        complete=ctx.complete,
        provenance=provenance,
        limit=3,
    )

    return FinalResult(
        status=status,
        question=question,
        answer=answer_text,
        provenance=provenance,
        findings=findings,
        papers_analyzed=len(findings),
        facts_stored=ctx.facts_stored,
        suggested_queries=suggested_queries,
    )


async def run_deep(
    question: str,
    config: AgentConfig,
    run_id: str,
    *,
    complete: CompleteFn,
    redis_client=None,
    caches=None,
    pdf_cache=None,
    findings: Optional[List[Finding]] = None,
    deadline: Optional[float] = None,
) -> AsyncIterator[AgentEvent]:
    """Run the deep agent for one question, yielding ProgressEvents then a single
    FinalResult. ``findings`` (if given) is populated as papers are analyzed so a
    caller enforcing an outer budget can salvage partial results on timeout."""
    import time

    # One directory per turn: run_id is the conversation id, so two turns of
    # the same conversation must not share (or delete) each other's papers.
    run_workdir = config.workdir / f"{run_id}-{uuid.uuid4().hex[:8]}"
    try:
        ctx = RunContext(
            config=config,
            complete=complete,
            question=question,
            redis_client=redis_client,
            caches=caches,
            pdf_cache=pdf_cache,
            run_workdir=run_workdir,
            deadline=(
                deadline
                if deadline is not None
                else time.monotonic() + config.total_budget_s
            ),
        )
        # Embedding-backed fact/answer store over the same LiteLLM proxy and Redis
        # client. Degrades to empty results when redis_client is None.
        from research_agent.core.llm import LiteLLMEmbedder
        from research_agent.knowledge import RedisKnowledgeStore

        embedding_route = config.model_route("embedding")
        embedder = LiteLLMEmbedder(
            embedding_route.base_url,
            embedding_route.api_key,
            embedding_route.model,
        )
        ctx.knowledge = RedisKnowledgeStore(redis_client, embedder.embed)
        if findings is not None:
            ctx.findings = findings  # share the list so the runner can salvage it

        agent = build_research_agent(config, ctx)

        final_text = ""
        async for mode, chunk in agent.astream(
            {"messages": [{"role": "user", "content": question}]},
            stream_mode=["updates", "custom"],
            config={
                "recursion_limit": RECURSION_LIMIT,
                "configurable": {"thread_id": run_id},
            },
        ):
            if mode == "custom":
                if isinstance(chunk, dict) and chunk.get("message"):
                    yield ProgressEvent(
                        message=chunk["message"], detail=chunk.get("detail")
                    )
            elif mode == "updates":
                text = _last_ai_text(chunk)
                if text:
                    final_text = text
                for event in translate_update(chunk, ctx):
                    yield event

        yield await build_final_result(question, final_text, ctx)
    finally:
        # Parsed papers are per-run scratch; durable state lives in Redis.
        shutil.rmtree(run_workdir, ignore_errors=True)
