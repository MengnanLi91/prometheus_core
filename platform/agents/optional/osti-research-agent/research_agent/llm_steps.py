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

"""LLM-driven steps of the OSTI literature search pipeline."""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional

import redis

from research_agent.core.llm import CompleteFn
from research_agent.prompt_templates import (
    ASSESS_ABSTRACT_PROMPT_TEMPLATE,
    EXTRACT_ANSWER_PROMPT_TEMPLATE,
    EXTRACT_FACTS_PROMPT_TEMPLATE,
    FOLLOWUP_QUERIES_PROMPT_TEMPLATE,
    SEARCH_QUERY_PROMPT_TEMPLATE,
    SYNTHESIZE_ANSWER_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)


def extract_json_from_text(text: str) -> Optional[Any]:
    """Extract JSON from text that may contain markdown code blocks."""
    # Try to find JSON in code blocks first
    json_match = re.search(r'```json\s*(\[.*?\]|\{.*?\})\s*```', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find raw JSON object or array
    json_match = re.search(r'(\[.*\]|\{.*\})', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


async def formulate_search_queries(question: str, complete: CompleteFn) -> str:
    """Use the configured LLM to generate an OSTI search query."""
    prompt = SEARCH_QUERY_PROMPT_TEMPLATE.format(question=question)

    try:
        response_text = await complete(prompt, max_tokens=256, temperature=0)

        return response_text.strip().strip('"\'')
    except Exception as e:
        logger.error(f"Error formulating search query: {e}")
        # Fallback: extract key terms from question
        return " ".join(question.split()[:7])


async def assess_abstract(
    question: str,
    title: str,
    abstract: str,
    complete: CompleteFn,
) -> Dict[str, Any]:
    """Use the configured LLM to assess if a paper is relevant."""
    prompt = ASSESS_ABSTRACT_PROMPT_TEMPLATE.format(
        question=question,
        title=title,
        abstract=abstract,
    )

    try:
        response_text = await complete(prompt, max_tokens=256, temperature=0)

        result = extract_json_from_text(response_text)
        return result or {"relevant": False, "confidence": 0.0, "reason": "Parse failed"}
    except Exception as e:
        logger.error(f"Error assessing abstract: {e}")
        return {"relevant": False, "confidence": 0.0, "reason": f"Error: {str(e)}"}


async def extract_answer_from_paper(
    question: str,
    paper_text: str,
    title: str,
    complete: CompleteFn,
) -> Dict[str, Any]:
    """Use the configured LLM to extract an answer from paper text."""
    # Limit paper text to avoid token limits
    paper_text = paper_text[:50000]

    prompt = EXTRACT_ANSWER_PROMPT_TEMPLATE.format(
        question=question,
        title=title,
        paper_text=paper_text,
    )

    try:
        response_text = await complete(prompt, max_tokens=2048, temperature=0)

        result = extract_json_from_text(response_text)
        return result or {"answered": False}
    except Exception as e:
        logger.error(f"Error extracting answer: {e}")
        return {"answered": False, "error": str(e)}


async def synthesize_final_answer(
    question: str,
    answers: List[Dict],
    complete: CompleteFn,
) -> str:
    """Synthesize a comprehensive answer from multiple papers."""
    answers_text = "\n\n".join(
        [
            f"Paper {i}: {ans['title']}\n"
            f"Answer: {ans['answer']}\n"
            f"Evidence: {ans.get('evidence', '')}"
            for i, ans in enumerate(answers, 1)
        ]
    )

    prompt = SYNTHESIZE_ANSWER_PROMPT_TEMPLATE.format(
        question=question,
        answers_count=len(answers),
        answers_text=answers_text,
    )

    try:
        return await complete(prompt, max_tokens=2048, temperature=0)
    except Exception as e:
        logger.error(f"Error synthesizing answer: {e}")
        # Fallback: concatenate answers
        return "\n\n".join([f"From {ans['title']}: {ans['answer']}" for ans in answers])


async def suggest_followup_queries(
    question: str,
    answer: str,
    complete: CompleteFn,
    provenance: Optional[List[Dict[str, Any]]] = None,
    limit: int = 3,
) -> List[Dict[str, str]]:
    """Generate 1-3 actionable follow-up literature queries for the UI."""
    prompt = FOLLOWUP_QUERIES_PROMPT_TEMPLATE.format(
        question=question,
        answer_summary=answer[:2000],
        source_titles_json=json.dumps(
            [p.get("title", "") for p in (provenance or [])][:10],
            indent=2,
        ),
        limit=limit,
    )

    suggestions: List[Dict[str, str]] = []
    seen = set()

    try:
        response_text = await complete(prompt, max_tokens=512, temperature=0)
        parsed = extract_json_from_text(response_text)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    candidate = str(item.get("suggested_query") or item.get("query") or "").strip()
                else:
                    candidate = str(item or "").strip()
                if not candidate:
                    continue
                key = candidate.lower()
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append({"suggested_query": candidate})
                if len(suggestions) >= limit:
                    break
    except Exception as exc:
        logger.warning(f"Failed to generate follow-up suggestions via model: {exc}")

    return suggestions


async def extract_and_store_facts(
    question: str,
    answers: List[Dict],
    complete: CompleteFn,
    redis_client: Optional[redis.Redis],
) -> int:
    """Extract discrete facts from answers and store in memory."""
    # Deferred import: memory.py imports extract_json_from_text from this module,
    # so importing memory at module scope here would create an import cycle.
    from research_agent.memory import store_atomic_fact

    answers_text = "\n\n".join([
        f"Paper: {ans['title']} (OSTI #{ans['osti_id']})\n"
        f"Answer: {ans['answer']}\n"
        f"Evidence: {ans.get('evidence', '')}"
        for ans in answers
    ])

    prompt = EXTRACT_FACTS_PROMPT_TEMPLATE.format(
        question=question,
        answers_text=answers_text,
    )

    try:
        response_text = await complete(prompt, max_tokens=2048, temperature=0)

        facts = extract_json_from_text(response_text)

        if not isinstance(facts, list):
            return 0

        stored_count = 0
        for fact_data in facts:
            # Find source paper details
            source_paper = next(
                (ans for ans in answers if ans["osti_id"] == fact_data.get("source_osti_id")),
                answers[0] if answers else {}
            )

            fact_id = await asyncio.to_thread(
                store_atomic_fact,
                content=fact_data["content"],
                source_paper=source_paper,
                topics=fact_data.get("topics", []),
                question=question,
                redis_client=redis_client,
            )
            if fact_id:
                stored_count += 1

        return stored_count
    except Exception as e:
        logger.error(f"Error extracting facts: {e}")
        return 0
