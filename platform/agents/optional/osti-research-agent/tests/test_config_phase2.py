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

"""Tests for parsing-related environment configuration and boolean values."""

from research_agent.core.config import AgentConfig, _env_bool


def test_from_env_reads_phase2_flags(monkeypatch):
    monkeypatch.setenv("REGISTRY_URL", "http://reg:4000")
    monkeypatch.setenv("PARSER", "legacy")
    monkeypatch.setenv("PARSE_OCR", "on")
    monkeypatch.setenv("ANALYZE_FIGURES", "true")
    monkeypatch.setenv("ANALYZER_MODEL_CALL_LIMIT", "6")
    monkeypatch.setenv("ANALYZER_TOOL_CALL_LIMIT", "10")

    cfg = AgentConfig.from_env()

    assert cfg.parser == "legacy"
    assert cfg.parse_ocr == "on"
    assert cfg.analyze_figures is True
    assert cfg.analyzer_model_call_limit == 6
    assert cfg.analyzer_tool_call_limit == 10


def test_from_env_phase2_defaults(monkeypatch):
    for var in (
        "PARSER",
        "PARSE_OCR",
        "ANALYZE_FIGURES",
        "ANALYZER_MODEL_CALL_LIMIT",
        "ANALYZER_TOOL_CALL_LIMIT",
        "LITELLM_BASE_URL",
        "REGISTRY_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    cfg = AgentConfig.from_env()

    assert cfg.parser == "artifacts"
    assert cfg.parse_ocr == "auto"
    assert cfg.analyze_figures is False
    assert cfg.analyzer_model_call_limit == 8
    assert cfg.analyzer_tool_call_limit == 12


def test_env_bool(monkeypatch):
    for truthy in ("1", "true", "TRUE", "yes", "on", " On "):
        monkeypatch.setenv("X_TEST_BOOL", truthy)
        assert _env_bool("X_TEST_BOOL", default=False) is True

    monkeypatch.setenv("X_TEST_BOOL", "nope")
    assert _env_bool("X_TEST_BOOL", default=True) is False

    monkeypatch.delenv("X_TEST_BOOL", raising=False)
    assert _env_bool("X_TEST_BOOL", default=True) is True
    assert _env_bool("X_TEST_BOOL", default=False) is False
