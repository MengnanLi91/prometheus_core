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

"""OSTI API search and paper download."""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import requests

if TYPE_CHECKING:
    # Only needed for the `cache` type hint below; deferred so this module
    # doesn't force a prometheus_api import at runtime just to type-check.
    from research_agent.memory import PdfCache

logger = logging.getLogger(__name__)

# Disable SSL warnings for OSTI (uses self-signed certs sometimes)
requests.packages.urllib3.disable_warnings()

_USER_AGENT = "osti-research-agent/1.0 (+https://www.osti.gov)"
_DEFAULT_MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB


def download_pdf_from_url(
    url: str,
    dest_path: str,
    *,
    max_bytes: int = _DEFAULT_MAX_PDF_BYTES,
    timeout: int = 120,
) -> Optional[str]:
    """Fetch an arbitrary PDF URL (e.g. an open-access link from scholarly or
    web search) to ``dest_path``. Returns the path, or None if the response
    isn't a PDF, is too large, or the request fails.

    A response counts as a PDF when its Content-Type is ``application/pdf`` or
    its bytes begin with the ``%PDF`` magic - guarding against HTML error pages
    served with a 200. Redirects are followed; a sane User-Agent is sent."""
    try:
        session = requests.Session()
        resp = session.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            verify=False,
        )
    except Exception as e:
        logger.warning(f"URL download failed for {url}: {e}")
        return None

    if resp.status_code != 200:
        logger.warning(f"URL download non-200 ({resp.status_code}) for {url}")
        return None

    content_length = resp.headers.get("Content-Length")
    if content_length and content_length.isdigit() and int(content_length) > max_bytes:
        logger.warning(f"PDF too large ({content_length} bytes > {max_bytes}) at {url}")
        return None

    content = resp.content
    if len(content) > max_bytes:
        logger.warning(f"PDF too large ({len(content)} bytes > {max_bytes}) at {url}")
        return None

    content_type = resp.headers.get("Content-Type", "")
    if "application/pdf" not in content_type.lower() and not content.startswith(
        b"%PDF"
    ):
        logger.warning(f"URL is not a PDF (Content-Type={content_type!r}) at {url}")
        return None

    try:
        with open(dest_path, "wb") as f:
            f.write(content)
    except OSError as e:
        logger.error(f"Could not write PDF to {dest_path}: {e}")
        return None
    return dest_path


def search_osti(search_query: str, max_results: int = 15) -> List[Dict[str, Any]]:
    """Search OSTI API and retrieve abstracts."""
    base_url = "https://www.osti.gov/api/v1/records"
    encoded_query = urllib.parse.quote_plus(search_query)

    results = []
    page = 0  # OSTI uses 0-based pagination

    while len(results) < max_results:
        try:
            full_url = f"{base_url}?q={encoded_query}&page={page}"
            logger.info(f"[OSTI API] Requesting: {full_url}")

            response = requests.get(full_url, timeout=60, verify=False)
            logger.info(f"[OSTI API] Response status: {response.status_code}")

            if response.status_code != 200:
                logger.warning(f"[OSTI API] Non-200 status: {response.status_code}")
                logger.warning(f"[OSTI API] Response body: {response.text[:500]}")
                break

            # Log raw response for debugging
            logger.info(f"[OSTI API] Response length: {len(response.text)} chars")

            try:
                data = json.loads(response.text.replace("\\'", "'"))
            except json.JSONDecodeError as e:
                logger.error(f"[OSTI API] JSON decode error: {e}")
                logger.error(f"[OSTI API] Raw response: {response.text[:1000]}")
                break

            if not data:
                logger.info(f"[OSTI API] No more results on page {page}")
                break

            logger.info(f"[OSTI API] Page {page}: Got {len(data)} records")

            for result in data:
                if "description" in result and "osti_id" in result:
                    results.append(
                        {
                            "osti_id": result["osti_id"],
                            "title": result.get("title", ""),
                            "description": result.get("description", ""),
                            "journal_name": result.get("journal_name", ""),
                            "url": f"https://www.osti.gov/biblio/{result['osti_id']}",
                        }
                    )
                    logger.info(
                        f"[OSTI API] Added: {result.get('title', 'No title')[:60]}..."
                    )

                    if len(results) >= max_results:
                        break

            page += 1

            # Safety limit to prevent infinite loops
            if page > 5:
                logger.info("[OSTI API] Reached page limit, stopping")
                break

        except Exception as e:
            logger.error(f"[OSTI API] Error searching OSTI: {e}", exc_info=True)
            break

    logger.info(f"[OSTI API] Total results found: {len(results)}")

    return results


def download_paper(osti_id: str, cache: Optional[PdfCache] = None) -> Optional[str]:
    """Download paper from OSTI, using `cache` (if provided) to avoid repeat downloads."""
    filepath = f"/tmp/osti_{osti_id}.pdf"

    # Check if file exists locally first
    if os.path.exists(filepath):
        logger.info(f"Paper {osti_id} found in local cache")
        return filepath

    # Check cache
    if cache is not None:
        try:
            pdf_bytes = cache.get_pdf(osti_id)
            if pdf_bytes:
                logger.info(
                    f"Paper {osti_id} found in Redis cache ({len(pdf_bytes)} bytes)"
                )
                with open(filepath, "wb") as f:
                    f.write(pdf_bytes)
                return filepath
        except Exception as e:
            logger.warning(f"Redis cache check failed: {e}")

    # Download from OSTI
    url = f"https://www.osti.gov/servlets/purl/{osti_id}"
    logger.info(f"Downloading paper {osti_id} from OSTI...")

    try:
        session = requests.Session()
        response = session.get(url, verify=False, timeout=120)

        if response.status_code == 200 and "application/pdf" in response.headers.get(
            "Content-Type", ""
        ):
            # Save to local file
            with open(filepath, "wb") as f:
                f.write(response.content)

            # Cache (30 day TTL)
            if cache is not None:
                try:
                    cache.put_pdf(osti_id, response.content, ttl_s=30 * 24 * 3600)
                    logger.info(
                        f"Paper {osti_id} cached in Redis ({len(response.content)} bytes)"
                    )
                except Exception as e:
                    logger.warning(f"Failed to cache paper in Redis: {e}")

            return filepath

        logger.warning(f"Paper {osti_id} not available (status={response.status_code})")
        return None
    except Exception as e:
        logger.error(f"Error downloading paper {osti_id}: {e}")
        return None
