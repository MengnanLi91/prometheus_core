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

"""Evidence weaker than a verified finding, kept apart from findings.

``record_abstract_evidence`` keeps a statement from the abstract of a paper
whose full text could not be read. Code checks it against the abstract the
search returned: the quote must appear in it word for word, and every number
in the statement (with its unit, when it has one) must appear in the quote.

These never enter ``RunContext.findings``, synthesis, or fact storage; the
final answer shows them in their own section, after the verified findings.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from langchain_core.tools import BaseTool, tool

from research_agent.tools import progress
from research_agent.tools.context import RunContext

MAX_ABSTRACT_EVIDENCE = 8

_NUMBER = r"[-+−]?\d+(?:[.,]\d+)?(?:[eE][-+−]?\d+)?"
_UNIT = r"(?:°\s*C|°C|MPa|GPa|kPa|ksi|dpa|ppm|wt\s*%|at\s*%|%|K|hr|h|s)"
_NUMBER_RE = re.compile(_NUMBER)
_QUANTITY_RE = re.compile(rf"({_NUMBER})\s*({_UNIT})(?![A-Za-z])")


def normalize(text: str) -> str:
    """Case, whitespace, minus signs and the degree sign, for comparison."""
    text = str(text or "").replace("−", "-").replace("º", "°").lower()
    return " ".join(text.split())


def numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUMBER_RE.findall(normalize(text))}


def quantities(text: str) -> set[tuple[str, str]]:
    """``{("15", "mpa"), ("400", "°c")}``: numbers with a unit right after."""
    return {
        (n.replace(",", "."), re.sub(r"\s+", "", unit))
        for n, unit in _QUANTITY_RE.findall(normalize(text))
    }


def supported_by(statement: str, source: str) -> Optional[str]:
    """Why ``statement`` states something ``source`` doesn't, or None."""
    missing_numbers = numbers(statement) - numbers(source)
    if missing_numbers:
        return f"numbers not in the cited text: {', '.join(sorted(missing_numbers))}"
    missing = quantities(statement) - quantities(source)
    if missing:
        shown = ", ".join(f"{n} {u}" for n, u in sorted(missing))
        return f"values with these units are not in the cited text: {shown}"
    return None


def _full_text_unavailable(ctx: RunContext, source_id: str, hit: dict) -> bool:
    if source_id in ctx.download_failures or source_id in ctx.parse_failures:
        return True
    is_osti = source_id.isdigit() or str(hit.get("origin") or "osti") == "osti"
    return not is_osti and not hit.get("oa_pdf_url")


def _abstract_of(hit: dict) -> str:
    # OSTI hits keep the raw API record, whose abstract is "description".
    return str(hit.get("abstract") or hit.get("description") or "")


def build_evidence_tools(ctx: RunContext) -> List[BaseTool]:
    @tool
    async def record_abstract_evidence(source_id: str, statement: str, quote: str) -> str:
        """Keep a statement from the ABSTRACT of a search result whose full text
        could not be read (its download or parse failed, or it has no
        downloadable PDF). ``quote`` must be copied word for word from that
        result's abstract, and every number in ``statement`` must appear in
        ``quote``. This is weaker than a finding from analyze_paper: use it only
        for papers you could not read, never instead of reading one."""
        source_id = str(source_id)
        hit = ctx.search_hits.get(source_id)

        def refuse(reason: str) -> str:
            return json.dumps({"ok": False, "source_id": source_id, "error": reason})

        if hit is None:
            return refuse("unknown source_id: use an id a search tool returned")
        abstract = _abstract_of(hit)
        if not abstract.strip():
            return refuse("this search result has no abstract")
        if any(f.source_id == source_id for f in ctx.findings):
            return refuse("this paper already has a verified finding; use that")
        if not _full_text_unavailable(ctx, source_id, hit):
            return refuse(
                "this paper's full text may be readable: download, parse and "
                "analyze it first; abstract evidence is only for papers that "
                "could not be read"
            )
        if not statement.strip() or not quote.strip():
            return refuse("statement and quote are both required")
        if normalize(quote) not in normalize(abstract):
            return refuse("the quote is not word for word in this paper's abstract")
        unsupported = supported_by(statement, quote)
        if unsupported:
            return refuse(f"the statement says more than the quote ({unsupported})")
        if len(ctx.abstract_evidence) >= MAX_ABSTRACT_EVIDENCE:
            return refuse(f"abstract evidence limit reached ({MAX_ABSTRACT_EVIDENCE})")

        ctx.abstract_evidence.append(
            {
                "source_id": source_id,
                "statement": " ".join(statement.split()),
                "quote": " ".join(quote.split()),
                "title": hit.get("title", ""),
                "url": hit.get("url", ""),
            }
        )
        progress.emit(
            f"Kept abstract evidence from {source_id} (full text unavailable)",
            {"source_id": source_id},
        )
        return json.dumps({"ok": True, "source_id": source_id})

    return [record_abstract_evidence]
