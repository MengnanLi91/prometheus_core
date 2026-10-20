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

"""Redis-backed knowledge base: cached answers, extracted atomic facts, and the
PDF/text caches used by the download and PDF-conversion steps.

Nothing here imports prometheus_api or a2a - `redis_client` and the two
caches are always plain redis-py objects (or None, when running without
Redis - e.g. `research_agent.cli --no-redis`), built by RedisCaches below
instead of prometheus_api.redis_utils.create_redis_client.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol

import redis

from research_agent.citations import is_memory_answer_sufficient
from research_agent.core.llm import CompleteFn
from research_agent.llm_steps import extract_json_from_text
from research_agent.prompt_templates import (
    SELECT_ATOMIC_FACTS_PROMPT_TEMPLATE,
    SYNTHESIZE_MEMORY_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)


class PdfCache(Protocol):
    def get_pdf(self, key: str) -> Optional[bytes]: ...
    def put_pdf(self, key: str, data: bytes, ttl_s: int) -> None: ...


class TextCache(Protocol):
    def get_text(self, key: str) -> Optional[str]: ...
    def put_text(self, key: str, text: str, ttl_s: int) -> None: ...


class ArtifactCache(Protocol):
    """Caches a whole parsed-paper artifact directory as a single tar blob,
    plus per-figure VLM JSON. Used by research_agent.parsing.pipeline."""

    def get_artifacts(self, key: str) -> Optional[bytes]: ...
    def put_artifacts(self, key: str, data: bytes, ttl_s: int) -> None: ...
    def get_figjson(self, source_id: str, idx: int) -> Optional[str]: ...
    def put_figjson(self, source_id: str, idx: int, data: str, ttl_s: int) -> None: ...


def _build_redis_client(redis_url: str, *, decode_responses: bool, password: Optional[str]) -> redis.Redis:
    return redis.Redis.from_url(redis_url, decode_responses=decode_responses, password=password)


class RedisCaches:
    """Wraps the text and binary Redis clients used for PDF/text caching."""

    def __init__(self, redis_url: Optional[str], redis_password: Optional[str] = None):
        self._redis_url = redis_url
        self._redis_password = redis_password
        self._client: Optional[redis.Redis] = None
        self._binary_client: Optional[redis.Redis] = None

    @property
    def client(self) -> redis.Redis:
        """Lazy initialization of the Redis client for text data."""
        if self._client is None:
            self._client = _build_redis_client(
                self._redis_url, decode_responses=True, password=self._redis_password
            )
        return self._client

    @property
    def binary_client(self) -> redis.Redis:
        """Lazy initialization of the Redis client for binary data (PDFs)."""
        if self._binary_client is None:
            self._binary_client = _build_redis_client(
                self._redis_url, decode_responses=False, password=self._redis_password
            )
        return self._binary_client

    def get_pdf(self, key: str) -> Optional[bytes]:
        return self.binary_client.get(f"osti:paper:pdf_bytes:{key}")

    def put_pdf(self, key: str, data: bytes, ttl_s: int) -> None:
        self.binary_client.set(f"osti:paper:pdf_bytes:{key}", data, ex=ttl_s)

    def get_text(self, key: str) -> Optional[str]:
        return self.client.get(f"osti:paper:text:v2:{key}")

    def put_text(self, key: str, text: str, ttl_s: int) -> None:
        self.client.set(f"osti:paper:text:v2:{key}", text, ex=ttl_s)

    # --- Parsed-artifact-directory cache (binary tar) ---

    def get_artifacts(self, key: str) -> Optional[bytes]:
        return self.binary_client.get(f"osti:paper:artifacts:v1:{key}")

    def put_artifacts(self, key: str, data: bytes, ttl_s: int) -> None:
        self.binary_client.set(f"osti:paper:artifacts:v1:{key}", data, ex=ttl_s)

    def get_figjson(self, source_id: str, idx: int) -> Optional[str]:
        return self.client.get(f"osti:paper:figjson:v1:{source_id}:{idx}")

    def put_figjson(self, source_id: str, idx: int, data: str, ttl_s: int) -> None:
        self.client.set(f"osti:paper:figjson:v1:{source_id}:{idx}", data, ex=ttl_s)


async def check_memory(
    question: str,
    complete: CompleteFn,
    redis_client: Optional[redis.Redis],
) -> Optional[Dict]:
    """Check if we've already answered this question or have relevant facts.

    Returns None immediately when there's no Redis - the caller falls back
    to a live search, exactly as if no cached answer or atomic facts existed.
    """
    if redis_client is None:
        return None

    # Hash question for lookup
    question_hash = hashlib.md5(question.lower().encode()).hexdigest()

    # Check for exact cached answer
    cached = await asyncio.to_thread(
        redis_client.get,
        f"osti:question:{question_hash}:answer",
    )
    if cached:
        logger.info(f"Found cached answer for question: {question}")
        cached_payload = json.loads(cached)
        cached_payload["sufficient"] = True
        cached_payload["memory_source"] = "cached_answer"
        return cached_payload

    # Check for relevant atomic facts.
    atomic_facts = await search_relevant_atomic_facts(
        question, complete, redis_client
    )

    if atomic_facts and len(atomic_facts) >= 2:
        logger.info("Found %d relevant atomic facts in memory", len(atomic_facts))
        synthesized = await synthesize_from_memory(question, atomic_facts, complete)
        if not synthesized:
            return None

        sufficient = bool(synthesized.get("sufficient_for_answer", False))
        if not sufficient:
            logger.info("Knowledge-base synthesis found insufficient information; will fall back to live OSTI search.")

        synthesized["sufficient"] = sufficient
        synthesized["memory_source"] = "atomic_fact_synthesis"
        return synthesized

    return None


async def search_relevant_atomic_facts(
    question: str,
    complete: CompleteFn,
    redis_client: Optional[redis.Redis],
) -> List[Dict]:
    """Find atomic facts relevant to the question."""
    if redis_client is None:
        return []

    # Existing records use the legacy ``osti:factoid:*`` key prefix. SCAN is
    # cursor-based and does not block Redis while building the match list.
    all_fact_keys = await asyncio.to_thread(
        lambda: list(redis_client.scan_iter(match="osti:factoid:*", count=200))
    )

    if not all_fact_keys:
        return []

    atomic_facts = []
    for key in all_fact_keys[:100]:  # Limit for performance
        data = await asyncio.to_thread(redis_client.get, key)
        if data:
            atomic_facts.append(json.loads(data))

    if not atomic_facts:
        return []

    # Use the configured model to select relevant ones
    selection_prompt = SELECT_ATOMIC_FACTS_PROMPT_TEMPLATE.format(
        question=question,
        atomic_facts_json=json.dumps(
            [
                {"id": fact["factoid_id"], "content": fact["content"]}
                for fact in atomic_facts
            ],
            indent=2,
        ),
    )

    try:
        response_text = await complete(selection_prompt, max_tokens=1024, temperature=0)

        selected_ids = extract_json_from_text(response_text) or []
        return [fact for fact in atomic_facts if fact["factoid_id"] in selected_ids]
    except Exception as e:
        logger.error(f"Error selecting relevant atomic facts: {e}")
        return []


async def synthesize_from_memory(
    question: str,
    atomic_facts: List[Dict],
    complete: CompleteFn,
) -> Optional[Dict]:
    """Synthesize an answer from stored atomic facts."""
    atomic_facts_text = "\n\n".join([
        f"Fact {i}: {f['content']}\n"
        f"Source: {f['source_paper'].get('title', 'Unknown')} (OSTI #{f['source_paper'].get('osti_id', 'N/A')})\n"
        f"Confidence: {f.get('confidence', 0.5)}"
        for i, f in enumerate(atomic_facts, 1)
    ])

    prompt = SYNTHESIZE_MEMORY_PROMPT_TEMPLATE.format(
        question=question,
        atomic_facts_text=atomic_facts_text,
    )

    try:
        raw_text = await complete(prompt, max_tokens=2048, temperature=0)

        parsed = extract_json_from_text(raw_text)
        if isinstance(parsed, dict):
            answer_text = str(parsed.get("answer", "")).strip() or raw_text.strip()
            confidence_raw = parsed.get("confidence", 0.0)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.0
            confidence = max(0.0, min(1.0, confidence))
            sufficient_raw = parsed.get("sufficient_for_answer")
            if isinstance(sufficient_raw, bool):
                sufficient = sufficient_raw
            else:
                sufficient = is_memory_answer_sufficient(answer_text)
            missing_information = str(parsed.get("missing_information", "") or "").strip()
        else:
            answer_text = raw_text.strip()
            confidence = 0.0
            sufficient = is_memory_answer_sufficient(answer_text)
            missing_information = ""

        return {
            "answer": answer_text,
            "sources": [
                {
                    "source": f"OSTI #{f['source_paper'].get('osti_id', 'N/A')}",
                    "osti_id": f['source_paper'].get('osti_id', ''),
                    "title": f['source_paper'].get('title', 'Unknown'),
                    "url": f['source_paper'].get('url', ''),
                    "excerpt": f['content'][:500],
                    "confidence": f.get('confidence', 0.5)
                }
                for f in atomic_facts
            ],
            "sufficient_for_answer": sufficient,
            "confidence": confidence,
            "missing_information": missing_information,
        }
    except Exception as e:
        logger.error(f"Error synthesizing from memory: {e}")
        return None


def cache_answer(
    question: str,
    answer: str,
    sources: List[Dict],
    redis_client: Optional[redis.Redis],
) -> None:
    """Cache question-answer pair for instant retrieval. No-op without Redis."""
    if redis_client is None:
        return

    question_hash = hashlib.md5(question.lower().encode()).hexdigest()

    cache_data = {
        "answer": answer,
        "sources": sources,
        "cached_at": datetime.now(timezone.utc).isoformat()
    }

    # Store with 30-day TTL
    redis_client.set(
        f"osti:question:{question_hash}:answer",
        json.dumps(cache_data),
        ex=30 * 24 * 3600,  # 30 days
    )


def store_atomic_fact(
    content: str,
    source_paper: Dict,
    topics: List[str],
    question: str,
    redis_client: Optional[redis.Redis],
) -> str:
    """Store a research finding in memory. No-op (returns "") without Redis."""
    if redis_client is None:
        return ""

    fact_id = str(uuid.uuid4())

    atomic_fact = {
        # Retain the legacy field name for persisted-data compatibility.
        "factoid_id": fact_id,
        "content": content,
        "source_paper": source_paper,
        "topics": topics,
        "confidence": 0.85,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "question_answered": question
    }

    # Retain the legacy Redis key prefix for persisted-data compatibility.
    redis_client.set(
        f"osti:factoid:{fact_id}",
        json.dumps(atomic_fact)
    )

    # Index by paper
    if source_paper.get("osti_id"):
        redis_client.sadd(
            f"osti:paper:{source_paper['osti_id']}:facts",
            fact_id
        )

    # Index by topics
    for topic in topics:
        redis_client.sadd(
            f"osti:topic:{topic}:factoids",
            fact_id
        )

    return fact_id
