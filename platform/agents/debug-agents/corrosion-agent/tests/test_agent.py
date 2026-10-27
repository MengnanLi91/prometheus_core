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

"""Input resolution for the corrosion agent."""
import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "corrosion_agent_module_inputs", Path(__file__).resolve().parents[1] / "agent.py"
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
resolve = _MODULE.CorrosionAnalysisAgent._resolve_inputs


def test_operating_conditions_come_nested_under_context():
    resolved = resolve({"material_id": "SS316", "context": {"temperature_c": 300, "coolant_chemistry": "PWR"}})

    assert resolved["material_id"] == "SS316"
    assert (resolved["temperature_c"], resolved["coolant_chemistry"]) == (300, "PWR")
    assert resolved["pressure_mpa"] is None


def test_a_top_level_form_answer_wins_over_the_nested_value():
    resolved = resolve({"material_id": "SS316", "temperature_c": 450, "context": {"temperature_c": 300}})

    assert resolved["temperature_c"] == 450


def test_a_non_dict_context_is_ignored():
    resolved = resolve({"material_id": "SS316", "context": "not a dict", "pressure_mpa": 15.5})

    assert resolved["temperature_c"] is None
    assert resolved["pressure_mpa"] == 15.5
