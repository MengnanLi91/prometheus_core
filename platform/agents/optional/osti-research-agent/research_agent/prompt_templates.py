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

"""Prompt template loading for the OSTI research agent."""

from pathlib import Path

PROMPTS_DIR = Path(__file__).with_name("prompts")


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


SEARCH_QUERY_PROMPT_TEMPLATE = _load_prompt("search_query.txt")
ASSESS_ABSTRACT_PROMPT_TEMPLATE = _load_prompt("assess_abstract.txt")
EXTRACT_ANSWER_PROMPT_TEMPLATE = _load_prompt("extract_answer.txt")
SYNTHESIZE_ANSWER_PROMPT_TEMPLATE = _load_prompt("synthesize_answer.txt")
FOLLOWUP_QUERIES_PROMPT_TEMPLATE = _load_prompt("followup_queries.txt")
SELECT_ATOMIC_FACTS_PROMPT_TEMPLATE = _load_prompt("select_atomic_facts.txt")
SYNTHESIZE_MEMORY_PROMPT_TEMPLATE = _load_prompt("synthesize_memory.txt")
EXTRACT_FACTS_PROMPT_TEMPLATE = _load_prompt("extract_facts.txt")
