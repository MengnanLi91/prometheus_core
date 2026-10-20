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

"""Runtime configuration for the research_agent core.

Deliberately independent of prometheus_api: reads the same environment
variables the docker-compose service already sets (REGISTRY_URL is the
LiteLLM proxy's base URL — it doubles as the A2A gateway and the LLM proxy,
see CLAUDE.md), but a bare `AgentConfig.from_env()` also works on a laptop
with nothing but LiteLLM credentials.
"""

import os
from pathlib import Path
from typing import Dict, Literal, Optional

from pydantic import BaseModel, Field


class ModelRoute(BaseModel):
    provider: Literal["openai", "anthropic"] = "openai"
    base_url: str
    api_key: Optional[str] = None
    model: str


class AgentConfig(BaseModel):
    litellm_base_url: str
    litellm_api_key: Optional[str] = None

    # Semantic model roles. Each defaults to the shared LiteLLM "medium" alias
    # and can be overridden through model_routes.
    orchestrator_model: str = "medium"
    analyzer_model: str = "medium"
    verifier_model: str = "medium"
    vlm_model: str = "medium"
    model_routes: Dict[str, ModelRoute] = Field(default_factory=dict)

    redis_url: Optional[str] = None
    redis_password: Optional[str] = None

    workdir: Path = Path("/tmp/research_agent")
    total_budget_s: int = 900
    max_papers_analyzed: int = 5
    analyzer_model_call_limit: int = Field(default=8, ge=1)
    analyzer_tool_call_limit: int = Field(default=12, ge=1)
    # The legacy pipeline remains selectable as a rollback path.
    engine: Literal["pipeline", "deepagent"] = "deepagent"

    # --- Internet search and embedding-backed memory ---
    # Web search is additive and OFF by default: pick a provider only where
    # egress allows it. "tavily" needs tavily_api_key + egress to tavily.com;
    # "searxng" points at a self-hosted instance (searxng_url). Scholarly search
    # (OpenAlex + arXiv) is keyless and always available.
    web_search_provider: Literal["none", "tavily", "searxng"] = "none"
    tavily_api_key: Optional[str] = None
    searxng_url: Optional[str] = None
    # OpenAlex asks callers to identify themselves via a mailto for the polite
    # pool; harmless when unset.
    openalex_mailto: Optional[str] = None
    # LiteLLM alias for the embedding model backing the knowledge store's
    # semantic atomic-fact and answer retrieval.
    embedding_model: str = "embedding"
    # PDF download cap for arbitrary (non-OSTI) URLs.
    max_pdf_mb: int = 50

    # --- PDF parsing ---
    # "artifacts" runs the docling-worker ladder (paper.md + per-table CSV/MD +
    # per-figure PNG/JSON); "legacy" keeps the old PDF->one-text-blob path
    # (pdf_convert.convert_pdf_to_text_async) as a rollback option.
    parser: Literal["artifacts", "legacy"] = "artifacts"
    docling_timeout_s: int = 240
    # OCR for scanned PDFs: "auto" turns it on only when the cheap PyMuPDF
    # pre-check finds near-zero extractable text; "on"/"off" force it.
    parse_ocr: Literal["auto", "on", "off"] = "auto"
    # Figure PNGs are always produced. Multimodal analysis writes
    # figures/*.json only when explicitly enabled; the pipeline engine does not
    # consume those JSON artifacts.
    analyze_figures: bool = False
    max_figures: int = 12
    # Skip figures smaller than this pixel area (logos, rules, decorations).
    min_figure_area: int = 10_000

    def model_route(
        self,
        role: Literal["orchestrator", "analyzer", "verifier", "vlm", "embedding"],
    ) -> ModelRoute:
        route = self.model_routes.get(role)
        if route is not None:
            return route
        return ModelRoute(
            base_url=self.litellm_base_url,
            api_key=self.litellm_api_key,
            model=getattr(self, f"{role}_model"),
        )

    @classmethod
    def from_env(cls) -> "AgentConfig":
        base_url = (
            os.getenv("LITELLM_BASE_URL")
            or os.getenv("REGISTRY_URL")
            or "http://localhost:4000"
        )
        return cls(
            litellm_base_url=base_url,
            litellm_api_key=os.getenv("LITELLM_MASTER_KEY"),
            redis_url=os.getenv("REDIS_URL"),
            redis_password=os.getenv("REDIS_PASSWORD"),
            engine=os.getenv("AGENT_ENGINE", "deepagent"),
            analyzer_model_call_limit=int(os.getenv("ANALYZER_MODEL_CALL_LIMIT", "8")),
            analyzer_tool_call_limit=int(os.getenv("ANALYZER_TOOL_CALL_LIMIT", "12")),
            parser=os.getenv("PARSER", "artifacts"),
            parse_ocr=os.getenv("PARSE_OCR", "auto"),
            analyze_figures=_env_bool("ANALYZE_FIGURES", default=False),
            web_search_provider=os.getenv("WEB_SEARCH_PROVIDER", "none"),
            tavily_api_key=os.getenv("TAVILY_API_KEY"),
            searxng_url=os.getenv("SEARXNG_URL"),
            openalex_mailto=os.getenv("OPENALEX_MAILTO"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "embedding"),
        )


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
