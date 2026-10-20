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

import json
from unittest.mock import MagicMock, patch

from research_agent import osti_client


def _response(status_code=200, text="[]"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def test_pagination_stops_after_page_limit():
    """No page ever satisfies max_results, so the safety limit (page > 5) stops the loop."""
    page_payload = json.dumps(
        [{"osti_id": "1", "title": "T", "description": "D", "journal_name": "J"}]
    )

    with patch.object(
        osti_client.requests, "get", return_value=_response(text=page_payload)
    ) as mock_get:
        results = osti_client.search_osti("creep rate", max_results=100)

    # Pages 0-5 inclusive = 6 requests before the "page > 5" safety limit breaks the loop.
    assert mock_get.call_count == 6
    assert len(results) == 6


def test_records_missing_description_or_osti_id_are_skipped():
    payload = json.dumps(
        [
            {
                "osti_id": "1",
                "title": "Has both",
                "description": "D",
                "journal_name": "J",
            },
            {"osti_id": "2", "title": "Missing description", "journal_name": "J"},
            {"title": "Missing osti_id", "description": "D", "journal_name": "J"},
        ]
    )

    responses = [_response(text=payload), _response(text="[]")]
    with patch.object(osti_client.requests, "get", side_effect=responses):
        results = osti_client.search_osti("creep rate", max_results=15)

    assert len(results) == 1
    assert results[0]["osti_id"] == "1"


def test_non_200_response_breaks_the_loop():
    with patch.object(
        osti_client.requests, "get", return_value=_response(status_code=500)
    ) as mock_get:
        results = osti_client.search_osti("creep rate", max_results=15)

    assert results == []
    mock_get.assert_called_once()


def test_json_repair_handles_escaped_apostrophe():
    # OSTI sometimes returns backslash-escaped apostrophes that aren't valid JSON escapes.
    payload = r"""[{"osti_id": "999", "title": "Bob\'s Paper", "description": "d", "journal_name": "j"}]"""

    responses = [_response(text=payload), _response(text="[]")]
    with patch.object(osti_client.requests, "get", side_effect=responses):
        results = osti_client.search_osti("creep rate", max_results=15)

    assert len(results) == 1
    assert results[0]["title"] == "Bob's Paper"
