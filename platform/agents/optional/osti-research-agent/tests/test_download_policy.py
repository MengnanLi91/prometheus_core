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

"""The download allowlist: which hosts PDFs may come from, how search results
and download_paper apply it, and how a 403 drops a host for the run."""

import asyncio
import json

import pytest

from research_agent import download_policy, osti_client
from research_agent.core.config import AgentConfig
from research_agent.tools import RunContext
from research_agent.tools import search as search_tools
from research_agent.tools.osti import build_osti_tools
from research_agent.tools.search import build_search_tools


async def _fake_complete(prompt, *, max_tokens):
    return ""


def _ctx(tmp_path, **overrides):
    config = AgentConfig(litellm_base_url="http://unused", **overrides)
    return RunContext(config=config, complete=_fake_complete, run_workdir=tmp_path)


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


# --- policy --------------------------------------------------------------


@pytest.mark.parametrize(
    "url, allowed",
    [
        ("https://www.osti.gov/servlets/purl/123", True),
        ("https://arxiv.org/pdf/2101.00001", True),
        ("https://export.arxiv.org/pdf/2101.00001", True),  # subdomain
        ("http://europepmc.org/articles/x.pdf", True),
        ("https://www.mdpi.com/1996-1944/14/10/2622/pdf", False),
        ("https://digital.library.unt.edu/ark:/67531/x/", False),
        ("https://evilarxiv.org/pdf/x", False),  # not a subdomain
        ("https://arxiv.org.evil.com/pdf/x", False),
        ("ftp://arxiv.org/pdf/x", False),
        ("", False),
        ("not a url", False),
    ],
)
def test_url_allowed(url, allowed):
    assert download_policy.url_allowed(url, download_policy.DEFAULT_ALLOWED_DOMAINS) is allowed


def test_domains_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("DOWNLOAD_ALLOWED_DOMAINS", " Example.org, .mdpi.com ,")
    assert AgentConfig.from_env().download_allowed_domains == ("example.org", "mdpi.com")

    monkeypatch.delenv("DOWNLOAD_ALLOWED_DOMAINS")
    assert AgentConfig.from_env().download_allowed_domains == (
        download_policy.DEFAULT_ALLOWED_DOMAINS
    )


# --- search results ------------------------------------------------------


def test_scholarly_results_only_offer_allowed_pdfs(monkeypatch, tmp_path):
    async def fake_scholarly(query, k, *, mailto=None, arxiv=True):
        return [
            {"source_id": "a", "title": "A", "oa_pdf_url": "https://arxiv.org/pdf/a"},
            {"source_id": "b", "title": "B", "oa_pdf_url": "https://www.mdpi.com/b/pdf"},
        ]

    monkeypatch.setattr(search_tools.scholarly, "search_scholarly", fake_scholarly)
    ctx = _ctx(tmp_path)
    scholarly = _tool(build_search_tools(ctx), "search_scholarly")

    out = json.loads(asyncio.run(scholarly.ainvoke({"query": "creep"})))

    assert [r["oa_pdf_url"] for r in out] == ["https://arxiv.org/pdf/a", None]
    assert ctx.search_hits["b"]["oa_pdf_url"] is None


# --- download_paper ------------------------------------------------------


def test_disallowed_host_is_skipped_without_a_request(monkeypatch, tmp_path):
    def must_not_fetch(*args, **kwargs):
        raise AssertionError("must not download from a disallowed host")

    monkeypatch.setattr(osti_client, "download_pdf_from_url", must_not_fetch)
    ctx = _ctx(tmp_path)
    ctx.search_hits["w"] = {"origin": "web", "url": "https://www.mdpi.com/x/pdf"}
    download = _tool(build_osti_tools(ctx), "download_paper")

    out = json.loads(
        asyncio.run(download.ainvoke({"source_id": "w", "url": "https://www.mdpi.com/x/pdf"}))
    )

    assert out["ok"] is False
    assert "www.mdpi.com is not on the download allowlist" in out["error"]
    assert "did not use up the paper budget" in out["instruction"]
    assert ctx.papers_attempted == 0


def test_a_403_drops_the_host_for_the_rest_of_the_run(monkeypatch, tmp_path):
    calls = []

    def refuse(url, dest, *, max_bytes, cache=None, on_status=None):
        calls.append(url)
        on_status(403)
        return None

    monkeypatch.setattr(osti_client, "download_pdf_from_url", refuse)
    ctx = _ctx(tmp_path)
    for sid in ("a", "b"):
        ctx.search_hits[sid] = {"origin": "openalex", "oa_pdf_url": f"https://zenodo.org/{sid}.pdf"}
    download = _tool(build_osti_tools(ctx), "download_paper")

    first = json.loads(asyncio.run(download.ainvoke({"source_id": "a"})))
    second = json.loads(asyncio.run(download.ainvoke({"source_id": "b"})))

    assert calls == ["https://zenodo.org/a.pdf"]  # the second is never requested
    assert ctx.refused_hosts == {"zenodo.org"}
    assert first["ok"] is False and second["ok"] is False
    assert "zenodo.org refused a download earlier in this run" in second["error"]


def test_other_failures_do_not_drop_the_host(monkeypatch, tmp_path):
    def not_found(url, dest, *, max_bytes, cache=None, on_status=None):
        on_status(404)
        return None

    monkeypatch.setattr(osti_client, "download_pdf_from_url", not_found)
    ctx = _ctx(tmp_path)
    ctx.search_hits["a"] = {"origin": "openalex", "oa_pdf_url": "https://zenodo.org/a.pdf"}

    asyncio.run(_tool(build_osti_tools(ctx), "download_paper").ainvoke({"source_id": "a"}))

    assert ctx.refused_hosts == set()


def test_osti_purl_downloads_are_always_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(osti_client, "download_paper", lambda sid, cache: f"/tmp/{sid}.pdf")
    ctx = _ctx(tmp_path, download_allowed_domains=("example.org",))  # no osti.gov

    out = json.loads(
        asyncio.run(_tool(build_osti_tools(ctx), "download_paper").ainvoke({"source_id": "42"}))
    )

    assert out == {"ok": True, "source_id": "42", "pdf_path": "/tmp/42.pdf"}


def test_status_callback_reports_non_200(tmp_path):
    from unittest.mock import MagicMock, patch

    resp = MagicMock(status_code=403, headers={})
    session = MagicMock()
    session.get.return_value = resp
    statuses = []
    with patch.object(osti_client.requests, "Session", return_value=session):
        out = osti_client.download_pdf_from_url(
            "https://zenodo.org/a.pdf", str(tmp_path / "a.pdf"), on_status=statuses.append
        )

    assert out is None and statuses == [403]
