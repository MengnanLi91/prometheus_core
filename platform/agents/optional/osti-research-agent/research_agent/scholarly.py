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

"""Scholarly search over OpenAlex (works index) and arXiv (preprints).

Both are keyless HTTP APIs, so ``search_scholarly`` is always available - it's
the fallback when OSTI is thin or the topic is outside DOE scope. Returns
normalized hit dicts carrying an ``oa_pdf_url`` when an open-access PDF exists,
which the generalized downloader can fetch directly.

Uses httpx (async) so it's exercised under respx in tests. arXiv's Atom feed is
parsed with the stdlib XML parser rather than adding a feedparser dependency.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
_TIMEOUT = 30
_USER_AGENT = "osti-research-agent/1.0 (+https://www.osti.gov)"

_ATOM = "{http://www.w3.org/2005/Atom}"


def _doi_slug(doi: Optional[str]) -> Optional[str]:
    """Normalize a DOI (URL or bare) to a filesystem-safe slug, or None."""
    if not doi:
        return None
    doi = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.lower().startswith(prefix):
            doi = doi[len(prefix) :]
            break
    doi = doi.strip("/")
    if not doi:
        return None
    return doi.replace("/", "_")


def _source_id(doi: Optional[str], url: str) -> str:
    slug = _doi_slug(doi)
    if slug:
        return slug
    return "url_" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def _reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """OpenAlex ships abstracts as an inverted index {word: [positions]} for
    copyright reasons; rebuild the running text."""
    if not inverted_index:
        return ""
    positioned: List[tuple] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            positioned.append((pos, word))
    positioned.sort(key=lambda p: p[0])
    return " ".join(word for _, word in positioned)


def _openalex_hit(work: dict) -> dict:
    doi = work.get("doi")
    landing = work.get("id", "")
    url = doi or landing
    best_oa = work.get("best_oa_location") or {}
    oa_pdf_url = best_oa.get("pdf_url")
    authors = [
        (a.get("author") or {}).get("display_name", "")
        for a in work.get("authorships", []) or []
    ]
    return {
        "source_id": _source_id(doi, url or landing),
        "title": work.get("title") or work.get("display_name") or "",
        "authors": [a for a in authors if a],
        "year": work.get("publication_year"),
        "abstract": _reconstruct_abstract(work.get("abstract_inverted_index")),
        "doi": _doi_slug(doi),
        "url": url or landing,
        "oa_pdf_url": oa_pdf_url,
        "origin": "openalex",
    }


async def search_openalex(
    query: str, k: int = 10, *, mailto: Optional[str] = None
) -> List[dict]:
    params = {"search": query, "per-page": max(1, min(k, 50))}
    if mailto:
        params["mailto"] = mailto
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = await client.get(OPENALEX_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("OpenAlex search failed: %s", e)
        return []
    return [_openalex_hit(w) for w in data.get("results", [])[:k]]


def _arxiv_entry(entry: ET.Element) -> dict:
    def text(tag: str) -> str:
        el = entry.find(f"{_ATOM}{tag}")
        return (el.text or "").strip() if el is not None and el.text else ""

    arxiv_url = text("id")
    title = " ".join(text("title").split())
    summary = " ".join(text("summary").split())
    published = text("published")
    year = published[:4] if len(published) >= 4 and published[:4].isdigit() else None
    authors = [
        (a.find(f"{_ATOM}name").text or "").strip()
        for a in entry.findall(f"{_ATOM}author")
        if a.find(f"{_ATOM}name") is not None
    ]
    pdf_url = None
    for link in entry.findall(f"{_ATOM}link"):
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = link.get("href")
            break
    return {
        "source_id": _source_id(None, arxiv_url),
        "title": title,
        "authors": [a for a in authors if a],
        "year": int(year) if year else None,
        "abstract": summary,
        "doi": None,
        "url": arxiv_url,
        "oa_pdf_url": pdf_url,
        "origin": "arxiv",
    }


async def search_arxiv(query: str, k: int = 5) -> List[dict]:
    params = {
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": max(1, min(k, 30)),
    }
    try:
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = await client.get(ARXIV_URL, params=params)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
    except Exception as e:
        logger.warning("arXiv search failed: %s", e)
        return []
    return [_arxiv_entry(e) for e in root.findall(f"{_ATOM}entry")][:k]


def _dedupe_key(hit: dict) -> str:
    if hit.get("doi"):
        return "doi:" + hit["doi"]
    return "title:" + "".join(c for c in hit.get("title", "").lower() if c.isalnum())


async def search_scholarly(
    query: str, k: int = 10, *, mailto: Optional[str] = None
) -> List[dict]:
    """OpenAlex first (comprehensive index), then arXiv preprints to fill in.
    Deduped by DOI/title, capped at ``k``."""
    openalex, arxiv = await asyncio.gather(
        search_openalex(query, k, mailto=mailto),
        search_arxiv(query, max(3, k // 3)),
    )

    merged: List[dict] = []
    seen = set()
    for hit in openalex + arxiv:
        key = _dedupe_key(hit)
        if key in seen or not hit.get("title"):
            continue
        seen.add(key)
        merged.append(hit)
        if len(merged) >= k:
            break
    return merged
