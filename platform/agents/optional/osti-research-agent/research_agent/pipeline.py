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

"""The full OSTI literature-search workflow: memory-first check, then (if
insufficient) query -> search -> assess -> download -> convert -> extract ->
synthesize -> store facts."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

import redis

from research_agent import citations, llm_steps, memory, osti_client, pdf_convert
from research_agent.core.config import AgentConfig
from research_agent.core.llm import CompleteFn
from research_agent.memory import ArtifactCache, PdfCache, TextCache
from research_agent.parsing import pipeline as parse_pipeline
from research_agent.parsing.artifacts import ParsedPaper, paper_dir

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 900  # 15 minutes maximum
MAX_PAPER_TEXT_CHARS = 50_000  # Preserve the legacy answer-extractor limit.

EmitFn = Callable[..., Awaitable[None]]


def _assemble_paper_text(parsed: ParsedPaper, base_dir) -> str:
    """Feed the answer extractor paper.md (truncated) plus the markdown of the
    three largest tables to retain structured evidence for table-heavy
    questions. Figure JSON is not consumed by this pipeline."""
    md = parsed.markdown_text(base_dir)[:MAX_PAPER_TEXT_CHARS]
    parts = [md]
    for table in parsed.largest_tables(3):
        try:
            table_md = (base_dir / table.md_path).read_text(encoding="utf-8")
        except OSError:
            continue
        label = table.caption or f"Table {table.index}"
        parts.append(f"\n\n=== {label} ===\n{table_md}")
    return "".join(parts)


async def answer_question(
    question: str,
    complete: CompleteFn,
    emit: EmitFn,
    redis_client: Optional[redis.Redis],
    pdf_cache: Optional[PdfCache] = None,
    text_cache: Optional[TextCache] = None,
    findings: Optional[List[Dict[str, Any]]] = None,
    config: Optional[AgentConfig] = None,
    caches: Optional[ArtifactCache] = None,
) -> Dict[str, Any]:
    """Memory-first entry point: answer from the knowledge base if it's
    sufficient, otherwise fall through to a live OSTI search."""
    await emit("Checking knowledge base for existing answers...")

    cached_answer = await memory.check_memory(question, complete, redis_client)

    if cached_answer and cached_answer.get("sufficient", True):
        # Cached answers may reference original paper numbering; preserve that
        # mapping while deduplicating to keep links accurate.
        provenance, paper_to_provenance = citations.deduplicate_provenance_with_mapping(cached_answer["sources"])
        answer = citations.linkify_paper_references(cached_answer["answer"], provenance, paper_to_provenance)

        suggested_queries = await llm_steps.suggest_followup_queries(
            question=question,
            answer=answer,
            complete=complete,
            provenance=provenance,
            limit=3,
        )

        return {
            "status": "completed",
            "question": question,
            "answer": answer,
            "source": "knowledge_base",
            "provenance": provenance,
            "cached": True,
            "suggested_queries": suggested_queries,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    if cached_answer and not cached_answer.get("sufficient", False):
        missing_information = cached_answer.get("missing_information")
        if missing_information:
            await emit(
                f"Knowledge base has partial information but is insufficient ({missing_information}). "
                "Continuing with live OSTI search..."
            )
        else:
            await emit(
                "Knowledge base has partial information but is insufficient. Continuing with live OSTI search..."
            )

    return await perform_literature_search(
        question, complete, emit, redis_client, pdf_cache, text_cache,
        findings=findings, config=config, caches=caches,
    )


async def perform_literature_search(
    question: str,
    complete: CompleteFn,
    emit: EmitFn,
    redis_client: Optional[redis.Redis],
    pdf_cache: Optional[PdfCache] = None,
    text_cache: Optional[TextCache] = None,
    findings: Optional[List[Dict[str, Any]]] = None,
    config: Optional[AgentConfig] = None,
    caches: Optional[ArtifactCache] = None,
) -> Dict[str, Any]:
    """Execute the full literature search workflow with timeout protection.

    `findings`, if given, is appended to as answers are found - it lets a
    caller (ResearchRunner) recover partial results if it has to cancel this
    coroutine on its own outer budget timeout.
    """
    start_time = time.time()
    # Monotonic deadline for the parse step's optional figure VLM pass, so it
    # can never eat into synthesis time (parse_paper_pipeline stops launching
    # figure calls as this approaches). Mirrors check_timeout's 60 s guard.
    parse_deadline = time.monotonic() + (TIMEOUT_SECONDS - 60)

    def check_timeout() -> bool:
        """Check if we're approaching timeout."""
        elapsed = time.time() - start_time
        return elapsed > (TIMEOUT_SECONDS - 60)  # Stop 1 min before timeout

    # Initialize state with progress log for UI feedback
    progress_log = []

    def log_progress(msg: str):
        """Add timestamped message to progress log."""
        elapsed = time.time() - start_time
        progress_log.append(f"[{elapsed:.1f}s] {msg}")
        logger.info(f"[STATUS] {msg}")

    state = {
        "question": question,
        "search_results": [],
        "answers_found": [],
        "status": "generating_query",
        "timeout_occurred": False
    }

    # Step 1: Generate search query
    log_progress("Formulating OSTI search query...")
    await emit("Formulating OSTI search query...")

    search_query = await llm_steps.formulate_search_queries(question, complete)
    state["search_query"] = search_query
    log_progress(f"Search query: '{search_query}'")
    await emit(f"OSTI search query: {search_query}")

    # Step 2: Search OSTI
    log_progress("Searching OSTI database...")
    await emit(f"Searching OSTI database for: '{search_query}'...")

    search_results = await asyncio.to_thread(osti_client.search_osti, search_query, max_results=15)
    state["search_results"] = search_results
    state["status"] = "assessing"

    log_progress(f"Found {len(search_results)} papers in OSTI database")
    await emit(f"Found {len(search_results)} results, assessing relevance...")
    if search_results:
        titles_block = "\n".join(
            f"{idx}. {item.get('title', 'Untitled')} (OSTI #{item.get('osti_id', 'N/A')})"
            for idx, item in enumerate(search_results, 1)
        )
        await emit(f"OSTI paper titles:\n{titles_block}")

    # Step 3: Assess and process each result
    relevant_count = 0
    papers_processed = 0

    for i, result in enumerate(search_results, 1):
        # Check timeout before processing each paper
        if check_timeout():
            log_progress("Approaching timeout, returning partial results...")
            await emit("Approaching timeout, returning partial results...")
            state["timeout_occurred"] = True
            break

        await emit(f"Assessing abstract {i}/{len(search_results)}...")

        assessment = await llm_steps.assess_abstract(
            question,
            result["title"],
            result["description"],
            complete,
        )

        result["assessment"] = assessment
        is_relevant = bool(assessment.get("relevant", False))
        confidence_raw = assessment.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        reason = assessment.get("reason", "No reason provided")
        await emit(
            f"Abstract assessment {i}/{len(search_results)}\n"
            f"Title: {result['title']}\n"
            f"Relevant: {'yes' if is_relevant else 'no'} (confidence {confidence:.0%})\n"
            f"Reason: {reason}"
        )

        if not is_relevant:
            continue

        relevant_count += 1
        short_title = result['title'][:50] + "..." if len(result['title']) > 50 else result['title']
        log_progress(f"Paper {relevant_count} relevant: {short_title}")
        await emit(
            f"Relevant paper selected for download:\n"
            f"Title: {result['title']}\n"
            f"OSTI ID: {result['osti_id']}\n"
            f"Abstract:\n{result.get('description', '')}"
        )

        # Download paper
        await emit(f"Downloading paper {relevant_count}: {short_title}")

        pdf_path = await asyncio.to_thread(osti_client.download_paper, result["osti_id"], pdf_cache)

        if not pdf_path:
            result["download_failed"] = True
            log_progress("  -> Download failed (PDF not available)")
            await emit(
                f"Download failed for OSTI #{result['osti_id']} ({result['title']})."
            )
            continue

        log_progress("  -> Downloaded successfully")
        await emit(
            f"Downloaded OSTI #{result['osti_id']} ({result['title']})."
        )

        # Convert to text (async with timeout)
        await emit(
            f"Converting paper {relevant_count} to text (this may take a few minutes)..."
        )

        use_artifacts = config is not None and config.parser == "artifacts"
        if use_artifacts:
            parsed = await parse_pipeline.parse_paper_pipeline(
                pdf_path, result["osti_id"], config, caches, emit,
                deadline=parse_deadline,
            )
            base_dir = paper_dir(config.workdir, result["osti_id"])
            paper_text = _assemble_paper_text(parsed, base_dir)
        else:
            paper_text = await pdf_convert.convert_pdf_to_text_async(pdf_path, text_cache, timeout=300)

        if not paper_text:
            result["conversion_failed"] = True
            log_progress("  -> Conversion failed or timed out")
            await emit(
                f"Conversion failed or timed out for OSTI #{result['osti_id']}."
            )
            continue

        log_progress(f"  -> Converted ({len(paper_text)} chars)")
        await emit(
            f"Converted OSTI #{result['osti_id']} to text ({len(paper_text)} chars)."
        )

        # Extract answer
        await emit(f"Extracting answer from paper {relevant_count}...")

        answer_data = await llm_steps.extract_answer_from_paper(
            question,
            paper_text,
            result["title"],
            complete,
        )

        if answer_data.get("answered", False):
            result["answer_data"] = answer_data
            found = {
                "osti_id": result["osti_id"],
                "title": result["title"],
                "url": result["url"],
                "answer": answer_data["answer"],
                "evidence": answer_data.get("evidence", ""),
                "excerpt": answer_data.get("excerpt", ""),
            }
            state["answers_found"].append(found)
            if findings is not None:
                findings.append(found)
            papers_processed += 1
            log_progress("  -> Extracted answer")
            await emit(f"Useful information found in OSTI #{result['osti_id']}.")
        else:
            log_progress("  -> No relevant answer found in paper")
            await emit(f"No useful information found in OSTI #{result['osti_id']}.")

        # Limit to 5 papers max for performance
        if papers_processed >= 5:
            log_progress("Reached paper limit (5), stopping search")
            break

    # Step 4: Synthesize final answer
    if not state["answers_found"]:
        # Build informative message about what was tried
        if len(search_results) == 0:
            no_results_msg = (
                f"No papers found in OSTI database for query: '{search_query}'. "
                "Try rephrasing with different keywords or broader terms."
            )
        elif relevant_count == 0:
            no_results_msg = (
                f"Found {len(search_results)} papers for '{search_query}', "
                "but none were assessed as relevant to your specific question."
            )
        else:
            no_results_msg = (
                f"Found {len(search_results)} papers, {relevant_count} seemed relevant, "
                "but could not extract answers (PDFs may not be available or readable)."
            )

        log_progress(no_results_msg)
        suggested_queries = await llm_steps.suggest_followup_queries(
            question=question,
            answer=no_results_msg,
            complete=complete,
            provenance=[],
            limit=3,
        )
        return {
            "status": "completed",
            "question": question,
            "answer": no_results_msg,
            "search_query": search_query,
            "papers_assessed": len(search_results),
            "relevant_papers": relevant_count,
            "papers_analyzed": 0,
            "progress_log": progress_log,
            "suggested_queries": suggested_queries,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    log_progress(f"Synthesizing answer from {len(state['answers_found'])} papers...")
    await emit(f"Synthesizing answer from {len(state['answers_found'])} papers...")

    final_answer = await llm_steps.synthesize_final_answer(question, state["answers_found"], complete)

    # Step 5: Extract and store facts
    log_progress("Storing facts in knowledge base...")
    await emit("Storing new facts in knowledge base...")

    facts_stored = await llm_steps.extract_and_store_facts(
        question,
        state["answers_found"],
        complete,
        redis_client,
    )

    # Step 6: Cache the answer
    await asyncio.to_thread(memory.cache_answer, question, final_answer, state["answers_found"], redis_client)

    elapsed = time.time() - start_time
    log_progress(f"Complete! ({elapsed:.1f}s total)")

    # Build deduplicated provenance with mapping from original paper numbers
    # This allows "Paper 5" in the text to be linked to the correct deduplicated source
    provenance, paper_to_provenance = citations.deduplicate_provenance_with_mapping(state["answers_found"])

    log_progress(f"Deduplicated {len(state['answers_found'])} papers to {len(provenance)} unique sources")

    # Convert "Paper X" citations to clickable links using the mapping
    final_answer = citations.linkify_paper_references(final_answer, provenance, paper_to_provenance)
    suggested_queries = await llm_steps.suggest_followup_queries(
        question=question,
        answer=final_answer,
        complete=complete,
        provenance=provenance,
        limit=3,
    )

    return {
        "status": "completed",
        "question": question,
        "answer": final_answer,
        "search_query": search_query,
        "papers_assessed": len(search_results),
        "relevant_papers": relevant_count,
        "papers_analyzed": len(state["answers_found"]),
        "provenance": provenance,
        "facts_stored": facts_stored,
        "progress_log": progress_log,
        "suggested_queries": suggested_queries,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "OSTI Research Agent"
    }
