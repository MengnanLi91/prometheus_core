// This file is part of Prometheus
// https://inl.gov/super-intelligence/prometheus/
//
// Copyright 2026 Battelle Energy Alliance, LLC
// All rights reserved, see NOTICE.txt for full restrictions
// https://github.com/idaholab/prometheus_core/blob/main/NOTICE.txt
//
// Licensed under the MIT License, please see LICENSE for details
// https://github.com/idaholab/prometheus_core/blob/main/LICENSE
//
// SPDX-License-Identifier: MIT

import { usePageHeader } from "@/lib/store/pageHeader";
import { WorkflowConsole } from "../components/workflows/WorkflowConsole";
import type { WorkflowTemplate } from "../components/workflows/WorkflowTemplateCard";

// Placeholder templates from Figma D.1. Comprehensive Material Analysis is the
// example workflow, so it's shown as selected.
const TEMPLATES: WorkflowTemplate[] = [
  {
    id: "comprehensive-material-analysis",
    name: "Comprehensive Material Analysis",
    complexity: "complex",
    checkpoints: 3,
    agents: 5,
    runtime: "1h 30m",
    speedup: "4.2x",
  },
  {
    id: "shielding-design-optimization",
    name: "Shielding Design Optimization",
    complexity: "complex",
    checkpoints: 2,
    agents: 4,
    runtime: "2h 45m",
    speedup: "6.1x",
  },
  {
    id: "component-stress-qualification",
    name: "Component Stress Qualification",
    complexity: "moderate",
    checkpoints: 4,
    agents: 3,
    runtime: "40m",
    speedup: "3.8x",
  },
  {
    id: "fuel-assembly-qa-pipeline",
    name: "Fuel Assembly QA Pipeline",
    complexity: "simple",
    checkpoints: 2,
    agents: 3,
    runtime: "10m",
    speedup: "5.5x",
  },
];
const SELECTED_TEMPLATE_ID = "comprehensive-material-analysis";

/**
 * Workflows section page: owns the page's data and header. Layout lives in
 * WorkflowConsole. Route params and filters will land here with the router.
 */
export function PageWorkflows() {
  usePageHeader({ title: "Workflow Console" });

  return <WorkflowConsole templates={TEMPLATES} selectedId={SELECTED_TEMPLATE_ID} />;
}
