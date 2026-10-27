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
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import redis
import yaml

from evals import report as render
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


def _select_questions(
    questions: List[Dict[str, Any]],
    ids: Optional[List[str]],
    categories: Optional[List[str]],
    limit: Optional[int],
) -> List[Dict[str, Any]]:
    """Filter by id and category (both optional), then apply --limit."""
    if ids:
        known = {str(q.get("id")) for q in questions}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise SystemExit(f"unknown question id(s): {', '.join(unknown)}")
        questions = [q for q in questions if str(q.get("id")) in ids]
    if categories:
        questions = [q for q in questions if q.get("category") in categories]
    if limit:
        questions = questions[:limit]
    if not questions:
        raise SystemExit("no questions match the given filters")
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
    on_record: Optional[Callable[[Dict[str, Any]], Any]] = None,
) -> List[Dict[str, Any]]:
    updates: Dict[str, Any] = {"engine": engine, "workdir": workdir / engine}
    if redis_db is not None:
        updates["redis_url"] = _redis_url_with_db(base.redis_url, redis_db)
    config = base.model_copy(update=updates)
    if redis_db is not None:
        _require_empty_redis(config, engine)

    records: List[Dict[str, Any]] = []
    for number, spec in enumerate(questions, 1):
        qid = spec["id"]
        print(
            f"[{engine} {number}/{len(questions)}] {qid}: {spec['question']}",
            file=sys.stderr,
        )
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
        if on_record is not None:
            on_record(record)
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
    parser.add_argument(
        "--id",
        dest="ids",
        action="append",
        default=None,
        help="only this question id (repeatable)",
    )
    parser.add_argument(
        "--category",
        dest="categories",
        action="append",
        default=None,
        help="only questions in this category (repeatable), e.g. core, uq",
    )
    parser.add_argument(
        "--markdown",
        default=None,
        help="Markdown report path (default: report.json's path with .md)",
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

    questions = _select_questions(
        _load_questions(Path(args.questions)), args.ids, args.categories, args.limit
    )

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

    report_path = Path(args.report)
    markdown_path = Path(args.markdown) if args.markdown else report_path.with_suffix(".md")
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    question_text = {str(q["id"]): str(q.get("question", "")) for q in questions}
    all_records: List[Dict[str, Any]] = []

    def write_reports(complete: bool) -> Dict[str, Any]:
        report = {
            "questions": len(questions),
            "engines": args.engines,
            "redis_db_base": args.redis_db_base,
            "started_at": started_at,
            "complete": complete,
            "records": all_records,
            "aggregate": scoring.aggregate(all_records),
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, default=str))
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            render.markdown_report(
                report,
                transcripts_dir=os.path.relpath(workdir, markdown_path.parent),
                questions=question_text,
            )
        )
        return report

    def on_record(record: Dict[str, Any]) -> None:
        # Rewritten after every question so a long run that dies keeps its results.
        all_records.append(record)
        write_reports(complete=False)

    try:
        for index, engine in enumerate(args.engines):
            redis_db = (
                args.redis_db_base + index if args.redis_db_base is not None else None
            )
            await _run_engine(
                engine, questions, base, workdir, redis_db, on_record=on_record
            )
    except KeyboardInterrupt:
        print("\nInterrupted; writing the partial report.", file=sys.stderr)
        write_reports(complete=False)
        raise

    report = write_reports(complete=True)
    print(render.terminal_summary(report))
    print(
        f"\nWrote {report_path}, {markdown_path} and transcripts under {workdir}/",
        file=sys.stderr,
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return asyncio.run(_main(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
