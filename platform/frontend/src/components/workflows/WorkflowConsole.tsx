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
import { SectionLabel } from "../ui/SectionLabel";
import { WorkflowTemplateCard, type WorkflowTemplate } from "./WorkflowTemplateCard";

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
 * Workflows landing page (Figma D.1): the template picker, then the execution
 * planner for the selected template. Display only for now; selection is fixed
 * to the example workflow.
 */
export function WorkflowConsole() {
  usePageHeader({ title: "Workflow Console" });

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-6">
      <section className="flex flex-col gap-3">
        <SectionLabel>Workflow Templates</SectionLabel>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {TEMPLATES.map((template) => (
            <WorkflowTemplateCard
              key={template.id}
              template={template}
              selected={template.id === SELECTED_TEMPLATE_ID}
            />
          ))}
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <SectionLabel>Workflow Execution Planner</SectionLabel>
        {/* TODO(PPLT-211): graph and plan summary. */}
      </section>
    </div>
  );
}
