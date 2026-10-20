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

"""The generalized URL downloader accepts real PDFs and rejects
HTML error pages, oversized bodies, and failed requests - without raising."""

from unittest.mock import MagicMock, patch

from research_agent import osti_client

_PDF_BYTES = b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n... body ..."


def _session(status_code=200, headers=None, content=b"", raises=None):
    """A fake requests.Session whose .get returns a canned response (or raises)."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.content = content

    session = MagicMock()
    if raises is not None:
        session.get.side_effect = raises
    else:
        session.get.return_value = resp
    return session


def _patch_session(session):
    # download_pdf_from_url calls requests.Session().get(...)
    return patch.object(osti_client.requests, "Session", return_value=session)


def test_accepts_pdf_by_content_type(tmp_path):
    dest = str(tmp_path / "p.pdf")
    session = _session(headers={"Content-Type": "application/pdf"}, content=_PDF_BYTES)
    with _patch_session(session):
        out = osti_client.download_pdf_from_url("https://x/p.pdf", dest)
    assert out == dest
    assert (tmp_path / "p.pdf").read_bytes() == _PDF_BYTES


def test_accepts_pdf_by_magic_bytes_when_content_type_missing(tmp_path):
    dest = str(tmp_path / "p.pdf")
    session = _session(headers={"Content-Type": "application/octet-stream"}, content=_PDF_BYTES)
    with _patch_session(session):
        out = osti_client.download_pdf_from_url("https://x/p", dest)
    assert out == dest


def test_rejects_html_page(tmp_path):
    dest = str(tmp_path / "p.pdf")
    session = _session(headers={"Content-Type": "text/html"}, content=b"<html>nope</html>")
    with _patch_session(session):
        out = osti_client.download_pdf_from_url("https://x/notpdf", dest)
    assert out is None
    assert not (tmp_path / "p.pdf").exists()


def test_rejects_oversized_by_content_length(tmp_path):
    dest = str(tmp_path / "p.pdf")
    session = _session(
        headers={"Content-Type": "application/pdf", "Content-Length": str(100 * 1024 * 1024)},
        content=_PDF_BYTES,
    )
    with _patch_session(session):
        out = osti_client.download_pdf_from_url("https://x/big.pdf", dest, max_bytes=50 * 1024 * 1024)
    assert out is None


def test_rejects_oversized_by_body_length(tmp_path):
    dest = str(tmp_path / "p.pdf")
    session = _session(headers={"Content-Type": "application/pdf"}, content=b"%PDF" + b"x" * 5000)
    with _patch_session(session):
        out = osti_client.download_pdf_from_url("https://x/big.pdf", dest, max_bytes=1000)
    assert out is None


def test_non_200_returns_none(tmp_path):
    dest = str(tmp_path / "p.pdf")
    session = _session(status_code=404, headers={"Content-Type": "application/pdf"}, content=_PDF_BYTES)
    with _patch_session(session):
        out = osti_client.download_pdf_from_url("https://x/missing.pdf", dest)
    assert out is None


def test_request_exception_returns_none(tmp_path):
    dest = str(tmp_path / "p.pdf")
    session = _session(raises=OSError("connection reset"))
    with _patch_session(session):
        out = osti_client.download_pdf_from_url("https://x/p.pdf", dest)
    assert out is None
