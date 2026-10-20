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

"""PDF-to-text conversion: PyMuPDF -> Docling -> marker fallback ladder, with text caching."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    # Only needed for the `cache` type hint below; deferred so this module
    # doesn't force a prometheus_api import at runtime just to type-check.
    from research_agent.memory import TextCache

logger = logging.getLogger(__name__)


def _convert_pdf_with_pymupdf(pdf_path: str) -> str:
    """Fast PDF text extraction using PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF

        logger.info(f"[pymupdf] Opening PDF: {pdf_path}")
        logger.info(f"[pymupdf] PyMuPDF version: {fitz.version}")

        # Use context manager for proper resource handling
        with fitz.open(pdf_path) as doc:
            page_count = doc.page_count
            logger.info(f"[pymupdf] PDF opened, {page_count} pages")

            text_parts = []
            total_chars = 0
            for page_num in range(page_count):
                try:
                    page = doc.load_page(page_num)
                    page_text = page.get_text()
                    char_count = len(page_text) if page_text else 0
                    total_chars += char_count
                    text_parts.append(page_text if page_text else "")

                    if page_num % 10 == 0:
                        logger.info(
                            f"[pymupdf] Page {page_num + 1}/{page_count}: {char_count} chars (total: {total_chars})"
                        )
                except Exception as page_error:
                    logger.error(
                        f"[pymupdf] Error on page {page_num + 1}: {page_error}"
                    )
                    text_parts.append("")

            full_text = "\n\n".join(text_parts)
            logger.info(
                f"[pymupdf] Extraction complete: {len(full_text)} chars from {page_count} pages"
            )
            return full_text

    except Exception as e:
        logger.error(f"[pymupdf] Error: {e}", exc_info=True)
        return ""


def _convert_pdf_with_marker_subprocess(pdf_path: str, timeout: int = 180) -> str:
    """Run marker conversion in a subprocess with hard timeout."""
    # Create a small Python script to run marker
    script = f"""
import sys
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TORCH_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

try:
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    converter = PdfConverter(artifact_dict=create_model_dict())
    rendered = converter("{pdf_path}")
    text, _, _ = text_from_rendered(rendered)
    print(text or "")
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

    try:
        logger.info(
            f"[marker-subprocess] Starting marker in subprocess (timeout: {timeout}s)..."
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "TORCH_NUM_THREADS": "1"},
        )

        if result.returncode == 0:
            text = result.stdout
            logger.info(f"[marker-subprocess] Success: {len(text)} chars")
            return text
        else:
            logger.error(f"[marker-subprocess] Failed: {result.stderr}")
            return ""

    except subprocess.TimeoutExpired:
        logger.error(f"[marker-subprocess] Timed out after {timeout}s")
        return ""
    except Exception as e:
        logger.error(f"[marker-subprocess] Error: {e}")
        return ""


def _convert_pdf_with_docling_subprocess(pdf_path: str, timeout: int = 180) -> str:
    """Run Docling conversion in a subprocess with hard timeout."""
    script = f"""
import sys
import os

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["TORCH_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

try:
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert("{pdf_path}")
    markdown = result.document.export_to_markdown()
    print(markdown or "")
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

    try:
        logger.info(
            f"[docling-subprocess] Starting Docling in subprocess (timeout: {timeout}s)..."
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                **os.environ,
                "CUDA_VISIBLE_DEVICES": "",
                "TORCH_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
        )

        if result.returncode == 0:
            text = result.stdout
            stripped = text.strip()
            if stripped and len(stripped) > 100:
                logger.info(f"[docling-subprocess] Success: {len(stripped)} chars")
                return stripped
            logger.warning(
                f"[docling-subprocess] Output insufficient ({len(stripped)} chars); falling back",
            )
            return ""

        logger.error(f"[docling-subprocess] Failed: {result.stderr}")
        return ""
    except subprocess.TimeoutExpired:
        logger.error(f"[docling-subprocess] Timed out after {timeout}s")
        return ""
    except Exception as e:
        logger.error(f"[docling-subprocess] Error: {e}")
        return ""


def _convert_pdf_with_marker(pdf_path: str) -> str:
    """PDF conversion using marker library (ML-based, better for complex layouts)."""
    try:
        # Force CPU mode and single-threaded to avoid hanging
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        os.environ["TORCH_NUM_THREADS"] = "1"
        os.environ["OMP_NUM_THREADS"] = "1"
        os.environ["MKL_NUM_THREADS"] = "1"

        logger.info("[marker] Importing marker modules (CPU mode)...")
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered

        logger.info("[marker] Imports complete")

        logger.info("[marker] Creating model dict...")
        model_dict = create_model_dict()
        logger.info("[marker] Model dict created")

        logger.info("[marker] Creating PdfConverter...")
        converter = PdfConverter(artifact_dict=model_dict)
        logger.info("[marker] PdfConverter created")

        logger.info(f"[marker] Calling converter({pdf_path})...")
        logger.info("[marker] This may take several minutes for large PDFs...")

        # Call converter - this is where it hangs
        rendered = converter(pdf_path)

        logger.info("[marker] Conversion returned, extracting text...")
        text, _, images = text_from_rendered(rendered)
        logger.info(f"[marker] Text extracted: {len(text) if text else 0} chars")
        return text or ""
    except Exception as e:
        logger.error(f"[marker] Error converting PDF: {e}")
        logger.error(f"[marker] Traceback: {traceback.format_exc()}")
        return ""


def convert_pdf_sync(pdf_path: str) -> str:
    """Synchronous PDF conversion - PyMuPDF first, then Docling, then marker."""
    # Check if file exists and log details
    logger.info(f"[pdf] PDF path: {pdf_path}")
    if os.path.exists(pdf_path):
        file_size = os.path.getsize(pdf_path)
        logger.info(
            f"[pdf] PDF exists, size: {file_size} bytes ({file_size/1024/1024:.2f} MB)"
        )
    else:
        logger.error(f"[pdf] PDF file does not exist: {pdf_path}")
        return ""

    # Try PyMuPDF first - it's fast and reliable
    logger.info("[pdf] Trying PyMuPDF extraction...")
    text = _convert_pdf_with_pymupdf(pdf_path)

    if text and len(text.strip()) > 100:
        logger.info("[pdf] PyMuPDF extraction successful")
        return text

    # PyMuPDF got no/little text - try Docling as the richer fallback
    logger.warning(
        f"[pdf] PyMuPDF got insufficient text ({len(text) if text else 0} chars)"
    )
    logger.info("[pdf] Trying Docling library as fallback...")

    docling_text = _convert_pdf_with_docling_subprocess(pdf_path, timeout=180)

    if docling_text:
        logger.info(f"[pdf] Docling extraction successful: {len(docling_text)} chars")
        return docling_text

    logger.info(
        "[pdf] Docling produced no or insufficient text; "
        "trying marker library as final fallback...",
    )

    marker_text = _convert_pdf_with_marker_subprocess(pdf_path, timeout=180)

    if marker_text and len(marker_text.strip()) > 100:
        logger.info(f"[pdf] Marker extraction successful: {len(marker_text)} chars")
        return marker_text

    logger.warning("[pdf] PyMuPDF, Docling, and marker all failed to extract text")
    return text or ""


async def convert_pdf_to_text_async(
    pdf_path: str,
    cache: Optional[TextCache] = None,
    timeout: int = 300,
) -> str:
    """Convert PDF to plain text, using `cache` (if provided) to avoid repeat conversions.

    Runs the CPU-intensive PyMuPDF/Docling/marker ladder in a thread pool
    with timeout.
    """
    # Extract OSTI ID from path for caching
    osti_id = os.path.basename(pdf_path).replace("osti_", "").replace(".pdf", "")

    # Check cache for previously converted text
    if cache is not None:
        try:
            cached_text = cache.get_text(osti_id)
            if cached_text:
                logger.info(f"Paper {osti_id} text found in cache")
                return cached_text
        except Exception as e:
            logger.warning(f"Redis text cache check failed: {e}")

    # Convert PDF in thread pool with timeout
    logger.info(f"Converting PDF {osti_id} to text (timeout: {timeout}s)...")
    try:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            text = await asyncio.wait_for(
                loop.run_in_executor(executor, convert_pdf_sync, pdf_path),
                timeout=timeout,
            )

        # Cache the converted text (30 day TTL)
        if text and cache is not None:
            try:
                cache.put_text(osti_id, text, ttl_s=30 * 24 * 3600)
                logger.info(f"Paper {osti_id} text cached ({len(text)} chars)")
            except Exception as e:
                logger.warning(f"Failed to cache text: {e}")

        return text
    except asyncio.TimeoutError:
        logger.error(f"PDF conversion timed out after {timeout}s for {osti_id}")
        return ""
    except Exception as e:
        logger.error(f"Error converting PDF: {e}", exc_info=True)
        return ""


def convert_pdf_to_text(pdf_path: str, cache: Optional[TextCache] = None) -> str:
    """Synchronous wrapper for backward compatibility."""
    return asyncio.get_event_loop().run_until_complete(
        convert_pdf_to_text_async(pdf_path, cache)
    )
