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

"""Parity evaluation runner: run every question through both engines and
score the results.

    # needs LiteLLM creds in the env (REGISTRY_URL/LITELLM_MASTER_KEY); Redis
    # optional but required for the cache-repeat questions to hit memory.
    uv run python -m evals.run --engines pipeline deepagent \
        --redis-db-base 14 --report evals/report.json

For LangSmith traces set LANGCHAIN_TRACING_V2=true + LANGCHAIN_API_KEY before
running (the deep engine's astream is already instrumented by langgraph).

This module is deliberately thin: it wires ResearchRunner to the offline
metrics in ``evals.scoring`` and prints a report. The LLM-judged rubric
(groundedness/completeness, 1-5) and the manual 5-transcript spot-check from the
plan are done separately against the transcripts this writes under --workdir.

Not unit-tested (it needs live services); the mechanical scoring it calls is
covered by tests/test_eval_scoring.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis
import yaml

from evals import scoring
from research_agent.core.config import AgentConfig
from research_agent.core.events import FinalResult, ProgressEvent
from research_agent.core.model_profile import apply_model_profile
from research_agent.core.runner import ResearchRunner


def _load_questions(path: Path) -> List[Dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    questions = data.get("questions", []) if isinstance(data, dict) else data
    if not questions:
        raise SystemExit(f"no questions found in {path}")
    return questions


def _configured_agent_config(llm_config: Any) -> AgentConfig:
    """Resolve the same registry/direct model profile used by the A2A agent."""
    if llm_config.proxy_url:
        llm_config = llm_config.with_api_key(os.getenv("LITELLM_MASTER_KEY"))
    return apply_model_profile(AgentConfig.from_env(), llm_config)


def _load_agent_config() -> AgentConfig:
    try:
        from prometheus_api.llm_client import LLMConfig
    except ImportError as exc:
        raise RuntimeError(
            "The parity evaluation must run in the OSTI agent runtime, "
            "where prometheus_api is installed."
        ) from exc

    return _configured_agent_config(LLMConfig.from_env())


def _redis_url_with_db(redis_url: str, db: int) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parsed = urlsplit(redis_url)
    return urlunsplit(parsed._replace(path=f"/{db}"))


def _require_empty_redis(config: AgentConfig, engine: str) -> None:
    client = redis.Redis.from_url(
        config.redis_url,
        password=config.redis_password,
        decode_responses=True,
    )
    try:
        size = client.dbsize()
    finally:
        client.close()
    if size:
        raise RuntimeError(
            f"Redis database selected for {engine} is not empty ({size} keys). "
            "Choose unused databases so engines cannot reuse prior answers."
        )


async def _run_one(
    config: AgentConfig, question: str, run_id: str
) -> tuple[Optional[FinalResult], float, List[str]]:
    runner = ResearchRunner(config)
    final: Optional[FinalResult] = None
    log: List[str] = []
    start = time.monotonic()
    async for event in runner.run(question, run_id=run_id):
        if isinstance(event, ProgressEvent):
            log.append(f"+{time.monotonic() - start:.1f}s {event.message}")
        else:
            final = event
    return final, time.monotonic() - start, log


async def _run_engine(
    engine: str,
    questions: List[Dict[str, Any]],
    base: AgentConfig,
    workdir: Path,
    redis_db: Optional[int],
) -> List[Dict[str, Any]]:
    updates: Dict[str, Any] = {"engine": engine, "workdir": workdir / engine}
    if redis_db is not None:
        updates["redis_url"] = _redis_url_with_db(base.redis_url, redis_db)
    config = base.model_copy(update=updates)
    if redis_db is not None:
        _require_empty_redis(config, engine)

    records: List[Dict[str, Any]] = []
    for spec in questions:
        qid = spec["id"]
        print(f"[{engine}] {qid}: {spec['question']}", file=sys.stderr)
        final, wall_s, log = await _run_one(
            config, spec["question"], f"eval-{engine}-{qid}"
        )
        result = (
            final.model_dump(mode="json")
            if final
            else {"status": "error", "answer": "no result"}
        )
        result["_engine"] = engine
        result["_wall_s"] = round(wall_s, 2)

        # Persist the full transcript for the manual spot-check.
        out_dir = workdir / engine / qid
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(json.dumps(result, indent=2, default=str))
        (out_dir / "progress.log").write_text("\n".join(log))

        record = scoring.score_result(result, spec)
        print(
            f"    -> status={record['status']} wall={record['wall_s']}s "
            f"facts={record['source_hit_rate']} cite={record['citation_link_rate']}",
            file=sys.stderr,
        )
        records.append(record)
    return records


async def _main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("--questions", default="evals/questions.yaml")
    parser.add_argument("--engines", nargs="+", default=["pipeline", "deepagent"])
    parser.add_argument("--workdir", default="evals/out")
    parser.add_argument("--report", default="evals/report.json")
    parser.add_argument(
        "--limit", type=int, default=None, help="only the first N questions"
    )
    parser.add_argument("--no-redis", action="store_true")
    parser.add_argument(
        "--redis-db-base",
        type=int,
        default=None,
        help=(
            "first empty Redis database reserved for this evaluation; each engine "
            "uses the next database so cached answers cannot cross engines"
        ),
    )
    args = parser.parse_args(argv)

    questions = _load_questions(Path(args.questions))
    if args.limit:
        questions = questions[: args.limit]

    base = _load_agent_config()
    if args.no_redis:
        if args.redis_db_base is not None:
            parser.error("--no-redis and --redis-db-base cannot be used together")
        base = base.model_copy(update={"redis_url": None})
    elif args.redis_db_base is not None and base.redis_url is None:
        parser.error("--redis-db-base requires REDIS_URL")
    elif len(args.engines) > 1 and args.redis_db_base is None:
        parser.error(
            "--redis-db-base is required for multi-engine runs so cached answers "
            "cannot cross engines"
        )
    if args.redis_db_base is not None and args.redis_db_base < 0:
        parser.error("--redis-db-base must be non-negative")
    workdir = Path(args.workdir)

    all_records: List[Dict[str, Any]] = []
    for index, engine in enumerate(args.engines):
        redis_db = (
            args.redis_db_base + index if args.redis_db_base is not None else None
        )
        all_records.extend(
            await _run_engine(engine, questions, base, workdir, redis_db)
        )

    report = {
        "questions": len(questions),
        "engines": args.engines,
        "redis_db_base": args.redis_db_base,
        "records": all_records,
        "aggregate": scoring.aggregate(all_records),
    }
    Path(args.report).write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report["aggregate"], indent=2))
    print(f"\nWrote {args.report} and transcripts under {workdir}/", file=sys.stderr)
    print(
        "Next: LLM-judged groundedness/completeness and a manual spot-check of 5 "
        "transcripts (see evals/README.md).",
        file=sys.stderr,
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(_main(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
