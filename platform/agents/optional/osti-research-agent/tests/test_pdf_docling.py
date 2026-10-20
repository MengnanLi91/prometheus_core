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

import asyncio
from unittest.mock import MagicMock

from research_agent import pdf_convert


class FakeTextCache:
    """Minimal TextCache double that mimics RedisCaches' key prefixing."""

    def __init__(self):
        self.values = {"osti:paper:text:5555": "old"}
        self.get_keys = []
        self.put_calls = []

    def get_text(self, key):
        full_key = f"osti:paper:text:v2:{key}"
        self.get_keys.append(full_key)
        return self.values.get(full_key)

    def put_text(self, key, text, ttl_s):
        full_key = f"osti:paper:text:v2:{key}"
        self.put_calls.append((full_key, text, ttl_s))
        self.values[full_key] = text


def test_pymupdf_used_first_docling_not_called(tmp_path, monkeypatch):
    pdf_path = tmp_path / "osti_1111.pdf"
    pdf_path.write_bytes(b"stub")

    monkeypatch.setattr(pdf_convert, "_convert_pdf_with_pymupdf", MagicMock(return_value="X" * 200))
    monkeypatch.setattr(pdf_convert, "_convert_pdf_with_docling_subprocess", MagicMock(return_value="DOC" * 200))
    monkeypatch.setattr(pdf_convert, "_convert_pdf_with_marker_subprocess", MagicMock(return_value="MARKER" * 200))

    text = pdf_convert.convert_pdf_sync(str(pdf_path))

    assert text == pdf_convert._convert_pdf_with_pymupdf.return_value
    pdf_convert._convert_pdf_with_docling_subprocess.assert_not_called()
    pdf_convert._convert_pdf_with_marker_subprocess.assert_not_called()


def test_falls_back_to_docling_when_pymupdf_insufficient(tmp_path, monkeypatch):
    pdf_path = tmp_path / "osti_2222.pdf"
    pdf_path.write_bytes(b"stub")

    monkeypatch.setattr(pdf_convert, "_convert_pdf_with_pymupdf", MagicMock(return_value="short text"))
    monkeypatch.setattr(pdf_convert, "_convert_pdf_with_docling_subprocess", MagicMock(return_value="DOC" * 200))
    monkeypatch.setattr(pdf_convert, "_convert_pdf_with_marker_subprocess", MagicMock(return_value="MARKER" * 200))

    text = pdf_convert.convert_pdf_sync(str(pdf_path))

    assert text == pdf_convert._convert_pdf_with_docling_subprocess.return_value
    pdf_convert._convert_pdf_with_docling_subprocess.assert_called_once()
    pdf_convert._convert_pdf_with_marker_subprocess.assert_not_called()


def test_falls_back_to_marker_when_pymupdf_and_docling_insufficient(tmp_path, monkeypatch):
    pdf_path = tmp_path / "osti_3333.pdf"
    pdf_path.write_bytes(b"stub")

    monkeypatch.setattr(pdf_convert, "_convert_pdf_with_pymupdf", MagicMock(return_value="short text"))
    monkeypatch.setattr(pdf_convert, "_convert_pdf_with_docling_subprocess", MagicMock(return_value=""))
    monkeypatch.setattr(pdf_convert, "_convert_pdf_with_marker_subprocess", MagicMock(return_value="MARKER" * 200))

    text = pdf_convert.convert_pdf_sync(str(pdf_path))

    assert text == pdf_convert._convert_pdf_with_marker_subprocess.return_value
    pdf_convert._convert_pdf_with_docling_subprocess.assert_called_once()
    pdf_convert._convert_pdf_with_marker_subprocess.assert_called_once()


def test_docling_subprocess_failure_returns_empty_and_does_not_raise(tmp_path, monkeypatch):
    pdf_path = tmp_path / "osti_4444.pdf"
    pdf_path.write_bytes(b"stub")

    class DummyResult:
        def __init__(self) -> None:
            self.returncode = 1
            self.stdout = ""
            self.stderr = "error"

    def fake_run(*args, **kwargs):
        return DummyResult()

    import subprocess

    monkeypatch.setattr(subprocess, "run", fake_run)

    text = pdf_convert._convert_pdf_with_docling_subprocess(str(pdf_path), timeout=1)

    assert text == ""


def test_async_uses_v2_cache_key(tmp_path, monkeypatch):
    pdf_path = tmp_path / "osti_5555.pdf"
    pdf_path.write_bytes(b"stub")

    fake_cache = FakeTextCache()
    monkeypatch.setattr(pdf_convert, "convert_pdf_sync", lambda path: "converted text")

    result = asyncio.run(pdf_convert.convert_pdf_to_text_async(str(pdf_path), cache=fake_cache))

    assert result == "converted text"
    assert fake_cache.get_keys[0] == "osti:paper:text:v2:5555"
    assert fake_cache.put_calls[0][0] == "osti:paper:text:v2:5555"
    assert fake_cache.values["osti:paper:text:5555"] == "old"
    assert fake_cache.values["osti:paper:text:v2:5555"] == "converted text"
